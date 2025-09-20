# main.py (修复版)
from flask import Flask, render_template, request, redirect, url_for, session, flash, jsonify
from werkzeug.utils import secure_filename
from flask_sqlalchemy import SQLAlchemy
from flask_migrate import Migrate
import cloudinary
import cloudinary.uploader
import cloudinary.api
import os
from datetime import datetime
from functools import wraps
from PIL import Image, ExifTags, UnidentifiedImageError
import io
import time
from cloudinary.utils import api_sign_request
from sqlalchemy.pool import NullPool, QueuePool
from sqlalchemy import text, func
import re, uuid

# --------------------------
# App & secret
# --------------------------
app = Flask(__name__)
app.secret_key = os.getenv('FLASK_SECRET', 'xia0720_secret')

# --------------------------
# Cloudinary 配置
# --------------------------
cloudinary.config(
    cloud_name=os.getenv('CLOUDINARY_CLOUD_NAME', 'dpr0pl2tf'),
    api_key=os.getenv('CLOUDINARY_API_KEY', '548549517251566'),
    api_secret=os.getenv('CLOUDINARY_API_SECRET', '9o-PlPBRQzQPfuVCQfaGrUV3_IE')
)

MAIN_ALBUM_FOLDER = os.getenv("MAIN_ALBUM_FOLDER", "albums")  # 若不想主文件夹，设置为空字符串 ""
MAX_CLOUDINARY_SIZE = 10 * 1024 * 1024  # 10MB

# ---------- Supabase 初始化（新增） ----------
# 需要安装 supabase 库：pip install supabase
from supabase import create_client
SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_KEY = os.getenv("SUPABASE_KEY")
SUPABASE_BUCKET = os.getenv("SUPABASE_BUCKET", "photos")
supabase = create_client(SUPABASE_URL, SUPABASE_KEY) if SUPABASE_URL and SUPABASE_KEY else None

def make_supabase_public_url(path):
    """从 Supabase 公共 bucket 构造可直接访问的 URL"""
    if not SUPABASE_URL or not SUPABASE_BUCKET:
        return None
    return f"{SUPABASE_URL.rstrip('/')}/storage/v1/object/public/{SUPABASE_BUCKET}/{path}"

def supabase_upload_file(path, file_like, content_type=None):
    """上传到 Supabase，返回 public url 或抛异常"""
    if not supabase:
        raise RuntimeError("Supabase 未配置 (SUPABASE_URL / SUPABASE_KEY)")
    file_like.seek(0)
    data = file_like.read()
    # 某些 supabase client 版本接受 bytes 或 file-like
    try:
        res = supabase.storage.from_(SUPABASE_BUCKET).upload(path, io.BytesIO(data), content_type)
    except Exception as e:
        # 有时 upload 会抛异常或返回 dict 带 error
        # 尝试再用 bytes
        try:
            res = supabase.storage.from_(SUPABASE_BUCKET).upload(path, data, content_type)
        except Exception:
            raise
    if isinstance(res, dict) and res.get("error"):
        raise Exception(res["error"])
    # 返回可直接访问的公共 URL（假设 bucket 是 public）
    return make_supabase_public_url(path)

def supabase_path_from_public_url(url):
    """从 Supabase 公共 URL 解析出 bucket 内的 path，用于删除"""
    if not url or not SUPABASE_BUCKET or not SUPABASE_URL:
        return None
    marker = f"/storage/v1/object/public/{SUPABASE_BUCKET}/"
    if marker in url:
        return url.split(marker, 1)[1]
    return None

# --------------------------
# 数据库配置
# --------------------------
database_url = os.getenv("DATABASE_URL")

if database_url:
    app.config['SQLALCHEMY_DATABASE_URI'] = database_url
    app.config['SQLALCHEMY_ENGINE_OPTIONS'] = {
        "poolclass": QueuePool,
        "pool_size": 5,
        "max_overflow": 10,
        "pool_timeout": 30,
        "pool_recycle": 1800
    }
else:
    app.config['SQLALCHEMY_DATABASE_URI'] = "sqlite:///stories.db"
    app.config['SQLALCHEMY_ENGINE_OPTIONS'] = {
        "poolclass": NullPool
    }

app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False

db = SQLAlchemy(app)
migrate = Migrate(app, db)

# 保证请求结束后释放 session
@app.teardown_appcontext
def shutdown_session(exception=None):
    db.session.remove()

# --------------------------
# 模型（在此统一定义，避免重复导入冲突）
# --------------------------
class Photo(db.Model):
    __tablename__ = "photo"
    id = db.Column(db.Integer, primary_key=True)
    album = db.Column(db.String(128), nullable=False)
    url = db.Column(db.String(512), nullable=False, unique=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    is_private = db.Column(db.Boolean, default=False)

class Story(db.Model):
    __tablename__ = "story"
    id = db.Column(db.Integer, primary_key=True)
    text = db.Column(db.Text, nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    images = db.relationship("StoryImage", backref="story", cascade="all, delete-orphan")

class StoryImage(db.Model):
    __tablename__ = "story_image"
    id = db.Column(db.Integer, primary_key=True)
    image_url = db.Column(db.String(500), nullable=False)
    story_id = db.Column(db.Integer, db.ForeignKey("story.id"), nullable=False)

# 确保 instance 文件夹存在
if not os.path.exists('instance'):
    os.makedirs('instance')

# 自动创建表（开发时有用；生产可以依赖 Alembic）
with app.app_context():
    db.create_all()

# --------------------------
# 模板上下文
# --------------------------
@app.context_processor
def inject_logged_in():
    return dict(logged_in=session.get("logged_in", False))

# --------------------------
# 工具：修正图片方向
# --------------------------
def fix_image_orientation(file_like):
    file_like.seek(0)
    img = Image.open(file_like)
    try:
        orientation_key = next((k for k, v in ExifTags.TAGS.items() if v == "Orientation"), None)
        exif = img._getexif()
        if exif and orientation_key:
            orientation_value = exif.get(orientation_key)
            if orientation_value == 3:
                img = img.rotate(180, expand=True)
            elif orientation_value == 6:
                img = img.rotate(270, expand=True)
            elif orientation_value == 8:
                img = img.rotate(90, expand=True)
    except Exception:
        pass
    return img

# --------------------------
# 登录保护
# --------------------------
def login_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if not session.get("logged_in"):
            return redirect(url_for("login", next=request.path))
        return f(*args, **kwargs)
    return decorated

# --------------------------
# 首页 & 静态页面
# --------------------------
@app.route("/")
def index():
    return render_template("index.html")

@app.route("/gallery")
def gallery():
    return render_template("gallery.html")

@app.route("/about")
def about():
    return render_template("about.html")

# --------------------------
# 相册列表（合并 Cloudinary + DB）
# --------------------------
@app.route("/album")
def albums():
    try:
        albums_list = []
        main = (MAIN_ALBUM_FOLDER or "").strip('/')

        # 1) Cloudinary 子文件夹（容错）
        try:
            if cloudinary.config().cloud_name:
                folders = cloudinary.api.subfolders(main) if main else cloudinary.api.subfolders('')
            else:
                folders = {"folders": []}
            album_names_cloud = [f.get("name") for f in folders.get("folders", [])]
        except Exception as e:
            print("Cloudinary subfolders fetch failed:", e)
            album_names_cloud = []

        # 2) DB 中的 album（非私密）
        db_album_covers = {}
        db_albums = db.session.query(Photo.album, Photo.url).filter(Photo.is_private == False).all()
        for a, url in db_albums:
            if a:
                db_album_covers.setdefault(a, url)

        album_set = set(album_names_cloud) | set(db_album_covers.keys())
        for album_name in sorted(album_set):
            cover = None
            try:
                if album_name in album_names_cloud:
                    r = cloudinary.api.resources(type="upload", prefix=f"{main}/{album_name}/" if main else f"{album_name}/", max_results=1)
                    if r.get('resources'):
                        cover = r['resources'][0].get('secure_url')
            except Exception:
                cover = None
            if not cover:
                cover = db_album_covers.get(album_name, "")
            albums_list.append({'name': album_name, 'cover': cover})

        return render_template("album.html", albums=albums_list)

    except Exception as e:
        print("ERROR in /album:", str(e))
        return f"Error fetching albums: {str(e)}"

# --------------------------
# 查看相册（Cloudinary + Supabase DB），并传 Drive 跳转入口
# --------------------------
@app.route("/album/<album_name>", methods=["GET", "POST"])
def view_album(album_name):
    try:
        main = (MAIN_ALBUM_FOLDER or "").strip('/')
        prefix = f"{main}/{album_name}" if main else album_name

        images = []

        # 1) Cloudinary 资源
        try:
            resources = cloudinary.api.resources(
                type="upload",
                prefix=prefix + "/",
                max_results=500
            )
            images += [
                {"public_id": img["public_id"], "secure_url": img["secure_url"], "source": "cloudinary"}
                for img in resources.get("resources", [])
                if img.get("public_id", "").startswith(prefix + "/")
            ]
        except Exception as e:
            print("Cloudinary list failed (ignored):", e)

        # 2) DB 中的 Supabase (is_private=False)
        db_imgs = Photo.query.filter_by(album=album_name, is_private=False).order_by(Photo.created_at.asc()).all()
        for p in db_imgs:
            if not any(item.get("secure_url") == p.url for item in images):
                images.append({"public_id": str(p.id), "secure_url": p.url, "source": "supabase"})

        logged_in = session.get("logged_in", False)

        # 3) Google Drive 链接入口（可改为按 album 配置）
        drive_link = os.getenv("DRIVE_ALBUM_BASE", "https://drive.google.com/drive/folders/1K_miEEKeQjw9pmmHBbJBzmEOg5l69zV_")

        return render_template(
            "view_album.html",
            album_name=album_name,
            images=images,
            logged_in=logged_in,
            drive_link=drive_link
        )

    except Exception as e:
        return f"Error loading album: {str(e)}"

# --------------------------
# Admin albums（改为基于 Photo 表，不依赖 Album 模型）
# --------------------------
@app.route("/admin/albums", methods=["GET", "POST"])
@login_required
def admin_albums():
    # 以 Photo 表中的 album 字段生成相册列表
    try:
        rows = db.session.query(
            Photo.album,
            func.count(Photo.id).label("count"),
            func.min(Photo.url).label("cover")
        ).group_by(Photo.album).all()

        albums = []
        for r in rows:
            albums.append({"name": r[0], "count": r[1], "cover": r[2]})
    except Exception as e:
        print("admin_albums error:", e)
        albums = []

    if request.method == "POST":
        # 简化：因为没有 Album 模型，我们不在这里修改数据结构
        flash("Cover update not implemented in this simplified admin view.", "info")
        return redirect(url_for("admin_albums"))

    return render_template("admin_albums.html", albums=albums)

# --------------------------
# 删除图片（登录）
# --------------------------
@app.route("/delete_images", methods=["POST"])
@login_required
def delete_images():
    selections = request.form.getlist("to_delete")
    album_name = request.form.get("album_name")
    if not selections:
        flash("No images selected for deletion.", "warning")
        return redirect(url_for("view_album", album_name=album_name))

    deleted_count = 0
    for sel in selections:
        try:
            source, identifier = sel.split("::", 1)
            if source == "cloudinary":
                try:
                    cloudinary.api.delete_resources([identifier])
                except Exception as e:
                    print("Cloudinary delete failed:", e)
            elif source == "supabase":
                photo = Photo.query.get(int(identifier))
                if photo:
                    # remove file from supabase storage if possible
                    path = supabase_path_from_public_url(photo.url)
                    if path and supabase:
                        try:
                            supabase.storage.from_(SUPABASE_BUCKET).remove([path])
                        except Exception as e:
                            print("Supabase remove failed:", e)
                    db.session.delete(photo)
            deleted_count += 1
        except Exception as e:
            print("Delete error:", e)

    db.session.commit()
    flash(f"Deleted {deleted_count} images successfully.", "success")
    return redirect(url_for("view_album", album_name=album_name))

# --------------------------
# Story 列表 / 详情 / 上传 / 编辑 / 删除
# --------------------------
@app.route("/story_list")
def story_list():
    stories = Story.query.order_by(Story.created_at.desc()).all()

    for story in stories:
        for img in story.images:
            if not img.image_url or not img.image_url.startswith("https://res.cloudinary.com/"):
                try:
                    filename = img.image_url.split("/")[-1]
                    public_id = filename.rsplit(".", 1)[0]
                    new_url, _ = cloudinary.utils.cloudinary_url(f"story/{public_id}")
                    img.image_url = new_url
                except Exception as e:
                    print(f"修复旧 Story 图片失败: {img.image_url} -> {e}")

    return render_template("story_list.html", stories=stories, logged_in=session.get("logged_in", False))

@app.route("/story/<int:story_id>")
def story_detail(story_id):
    story = Story.query.get_or_404(story_id)
    return render_template("story_detail.html", story=story)

@app.route("/upload_story", methods=["GET", "POST"])
@login_required
def upload_story():
    if request.method == "POST":
        story_text = request.form.get("story_text")
        files = request.files.getlist("story_images")

        if not story_text or story_text.strip() == "":
            flash("Story content is required.", "error")
            return redirect(request.url)

        new_story = Story(text=story_text.strip())
        db.session.add(new_story)
        db.session.flush()

        for file in files:
            if file and file.filename:
                try:
                    file.stream.seek(0, io.SEEK_END)
                    size = file.stream.tell()
                    file.stream.seek(0)

                    if size > 9.5 * 1024 * 1024:
                        img = Image.open(file.stream)
                        img = img.convert("RGB")
                        buf = io.BytesIO()
                        img.save(buf, format="JPEG", quality=85, optimize=True)
                        buf.seek(0)
                        upload_buffer = buf
                    else:
                        upload_buffer = file.stream

                    base = secure_filename(file.filename.rsplit('.', 1)[0])
                    ext = file.filename.rsplit('.', 1)[-1].lower() if '.' in file.filename else 'jpg'
                    path = f"stories/{base}.{ext}"
                    public_url = supabase_upload_file(path, upload_buffer, content_type=file.mimetype)

                    db.session.add(StoryImage(image_url=public_url, story=new_story))
                except Exception as e:
                    print(f"上传故事图片失败: {e}")
                    flash(f"One image failed to upload: {file.filename}", "error")

        db.session.commit()
        flash("Story uploaded successfully!", "success")
        return redirect(url_for("story_list"))

    return render_template("upload_story.html")

@app.route("/story/<int:story_id>/edit", methods=["GET", "POST"])
@login_required
def edit_story(story_id):
    story = Story.query.get_or_404(story_id)

    if request.method == "POST":
        text = request.form.get("text")
        if not text or text.strip() == "":
            flash("Story content cannot be empty", "error")
            return render_template("edit_story.html", story=story)

        story.text = text.strip()

        delete_image_ids = request.form.get("delete_images", "")
        if delete_image_ids:
            for img_id in delete_image_ids.split(","):
                img = StoryImage.query.get(int(img_id))
                if img:
                    path = supabase_path_from_public_url(img.image_url)
                    if path and supabase:
                        try:
                            supabase.storage.from_(SUPABASE_BUCKET).remove([path])
                        except Exception as e:
                            print("Supabase remove failed:", e)
                    db.session.delete(img)

        files = request.files.getlist("story_images")
        for file in files:
            if file and file.filename:
                try:
                    file.stream.seek(0, io.SEEK_END)
                    size = file.stream.tell()
                    file.stream.seek(0)

                    if size > 9.5 * 1024 * 1024:
                        img = Image.open(file.stream)
                        img = img.convert("RGB")
                        buf = io.BytesIO()
                        img.save(buf, format="JPEG", quality=85, optimize=True)
                        buf.seek(0)
                        upload_buffer = buf
                    else:
                        upload_buffer = file.stream

                    base = secure_filename(file.filename.rsplit('.', 1)[0])
                    ext = file.filename.rsplit('.', 1)[-1].lower() if '.' in file.filename else 'jpg'
                    path = f"stories/{base}.{ext}"
                    public_url = supabase_upload_file(path, upload_buffer, content_type=file.mimetype)

                    db.session.add(StoryImage(image_url=public_url, story=story))
                except Exception as e:
                    print(f"编辑 Story 上传图片失败: {e}")
                    flash(f"Image {file.filename} failed to upload", "error")

        db.session.commit()
        flash("Story updated", "success")
        return redirect(url_for("story_detail", story_id=story.id))

    return render_template("edit_story.html", story=story)

@app.route("/delete_story/<int:story_id>", methods=["POST"])
@login_required
def delete_story(story_id):
    story = Story.query.get_or_404(story_id)
    db.session.delete(story)
    db.session.commit()
    flash("Story deleted.", "info")
    return redirect(url_for("story_list"))

# --------------------------
# 上传图片（将文件上传到 Supabase；前端再通过 save_photo 写 DB）
# --------------------------
@app.route("/upload", methods=["GET", "POST"])
def upload():
    if request.method == "POST":
        album_name = request.form.get("album") or request.form.get("new_album")
        files = request.files.getlist("photo")
        uploaded_urls = []

        for file in files:
            if file and file.filename:
                try:
                    folder_path = f"{MAIN_ALBUM_FOLDER}/{album_name}" if MAIN_ALBUM_FOLDER else album_name
                    base = secure_filename(file.filename.rsplit('.', 1)[0])
                    ext = file.filename.rsplit('.', 1)[-1].lower() if '.' in file.filename else 'jpg'
                    file_path = f"{folder_path}/{base}.{ext}"

                    file.stream.seek(0, io.SEEK_END)
                    size = file.stream.tell()
                    file.stream.seek(0)
                    upload_buffer = file.stream

                    if size > MAX_CLOUDINARY_SIZE and (file.mimetype or "").startswith("image"):
                        img = Image.open(file.stream)
                        img = img.convert("RGB")
                        out = io.BytesIO()
                        img.save(out, format="JPEG", quality=85, optimize=True)
                        out.seek(0)
                        upload_buffer = out

                    public_url = supabase_upload_file(file_path, upload_buffer, content_type=file.mimetype)
                    uploaded_urls.append(public_url)

                except Exception as e:
                    print(f"上传失败 {file.filename}: {e}")

        return jsonify({"success": True, "urls": uploaded_urls})

    # GET: 获取可选相册（仅从 Cloudinary 列出）
    album_names_set = set()
    main_prefix = (MAIN_ALBUM_FOLDER or "").strip(' /')

    try:
        next_cursor = None
        while True:
            resources = cloudinary.api.resources(
                type="upload",
                prefix=f"{main_prefix}/" if main_prefix else "",
                max_results=500,
                next_cursor=next_cursor
            )
            for res in resources.get('resources', []):
                public_id = res.get('public_id', '')
                parts = public_id.split('/')
                if main_prefix:
                    if parts[0] == main_prefix and len(parts) >= 2:
                        album_names_set.add(parts[1])
                else:
                    if len(parts) >= 1:
                        album_names_set.add(parts[0])

            next_cursor = resources.get('next_cursor')
            if not next_cursor:
                break

        album_names = sorted(album_names_set)
    except Exception as e:
        print(f"获取相册失败: {e}")
        album_names = []

    return render_template(
        "upload.html",
        album_names=album_names,
        MAIN_ALBUM_FOLDER=MAIN_ALBUM_FOLDER,
        last_album=""
    )

# --------------------------
# 私密上传（仅登录） -> 上传到 Supabase 并写 Photo(is_private=True)
# --------------------------
@app.route("/upload_private", methods=["POST"])
@login_required
def upload_private():
    import io
    from PIL import Image, ExifTags

    album_name = request.form.get("album")
    if album_name == "new":
        album_name = (request.form.get("new_album") or "").strip()
        if not album_name:
            return jsonify({"success": False, "error": "相册名不能为空"}), 400

    safe_album = re.sub(r'[^a-zA-Z0-9_-]', '_', album_name).strip('_') or "default"
    files = request.files.getlist("photo")
    if not files or all(f.filename == '' for f in files):
        return jsonify({"success": False, "error": "请选择至少一张照片"}), 400

    uploaded_urls = []
    for file in files:
        if not file or file.filename == '':
            continue
        try:
            base_name = file.filename.rsplit('.', 1)[0]
            safe_name = re.sub(r'[^a-zA-Z0-9_-]', '_', base_name).strip('_') or str(uuid.uuid4())
            ext = file.filename.rsplit('.', 1)[-1].lower() if '.' in file.filename else 'jpg'
            file.stream.seek(0)
            raw = file.read()
            upload_buffer = io.BytesIO(raw)
            mimetype = (file.mimetype or "").lower()

            # 图片压缩逻辑（超过 MAX_CLOUDINARY_SIZE）
            if len(raw) > MAX_CLOUDINARY_SIZE and mimetype.startswith("image"):
                img = Image.open(io.BytesIO(raw))
                try:
                    orientation_key = next((k for k, v in ExifTags.TAGS.items() if v == "Orientation"), None)
                    exif = img._getexif()
                    if exif and orientation_key:
                        o = exif.get(orientation_key)
                        if o == 3:
                            img = img.rotate(180, expand=True)
                        elif o == 6:
                            img = img.rotate(270, expand=True)
                        elif o == 8:
                            img = img.rotate(90, expand=True)
                except Exception:
                    pass

                max_dim = 3000
                w, h = img.size
                if max(w, h) > max_dim:
                    if w >= h:
                        new_w = max_dim
                        new_h = int(h * max_dim / w)
                    else:
                        new_h = max_dim
                        new_w = int(w * max_dim / h)
                    img = img.resize((new_w, new_h), Image.LANCZOS)

                quality = 90
                out = io.BytesIO()
                img.convert("RGB").save(out, format="JPEG", quality=quality, optimize=True)
                while out.tell() > MAX_CLOUDINARY_SIZE and quality > 30:
                    quality -= 10
                    out.seek(0); out.truncate(0)
                    img.convert("RGB").save(out, format="JPEG", quality=quality, optimize=True)

                while out.tell() > MAX_CLOUDINARY_SIZE:
                    w, h = img.size
                    img = img.resize((max(200, int(w * 0.8)), max(200, int(h * 0.8))), Image.LANCZOS)
                    out.seek(0); out.truncate(0)
                    img.convert("RGB").save(out, format="JPEG", quality=quality, optimize=True)
                    if img.size[0] < 400 or img.size[1] < 400:
                        break

                out.seek(0)
                upload_buffer = out
            elif len(raw) > MAX_CLOUDINARY_SIZE and not mimetype.startswith("image"):
                return jsonify({"success": False, "error": f"文件 {file.filename} 太大且不是图片"}), 400

            folder_path = f"private/{safe_album}"
            path = f"{folder_path}/{safe_name}.{ext}"
            upload_buffer.seek(0)
            public_url = supabase_upload_file(path, upload_buffer, content_type=mimetype)

            new_photo = Photo(
                album=album_name,
                url=public_url,
                is_private=True,
                created_at=datetime.utcnow()
            )
            db.session.add(new_photo)
            db.session.commit()
            uploaded_urls.append(public_url)

        except Exception as e:
            db.session.rollback()
            return jsonify({"success": False, "error": f"上传失败 {file.filename}: {e}"}), 500

    return jsonify({"success": True, "urls": uploaded_urls, "album": album_name})

# --------------------------
# 登录/登出
# --------------------------
@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        username = request.form.get("username", "")
        password = request.form.get("password", "")
        if username == "xia0720" and password == "qq123456":
            session["logged_in"] = True
            flash("Logged in.")
            next_url = request.args.get("next")
            return redirect(next_url or url_for("story_list"))
        else:
            flash("Invalid credentials.")
            return redirect(url_for("login"))
    return render_template("login.html")

@app.route("/logout")
def logout():
    session.pop("logged_in", None)
    flash("Logged out.")
    return redirect(url_for("index"))

# --------------------------
# DB 测试
# --------------------------
@app.route("/test-db")
def test_db():
    try:
        db.session.execute(text("SELECT 1"))
        return "DB OK"
    except Exception as e:
        return f"DB failed: {str(e)}", 500

# --------------------------
# Private-space（仅登录）
# --------------------------
@app.route("/private_space")
@login_required
def private_space():
    album_names_set = set()
    album_covers = {}

    try:
        photos = Photo.query.filter_by(is_private=True).order_by(Photo.created_at.desc()).all()
        for p in photos:
            album = p.album or "default"
            album_names_set.add(album)
            if album not in album_covers:
                album_covers[album] = p.url

        album_names = sorted(album_names_set)
    except Exception as e:
        print(f"获取私密相册失败: {e}")
        album_names = []
        album_covers = {}

    return render_template(
        "private_album.html",
        album_names=album_names,
        album_covers=album_covers,
        last_album=session.get("last_private_album", "")
    )

@app.route("/private_space/<album_name>", methods=["GET", "POST"])
@login_required
def view_private_album(album_name):
    try:
        images = []
        photos = Photo.query.filter_by(album=album_name, is_private=True).order_by(Photo.created_at.asc()).all()
        for p in photos:
            images.append({"public_id": None, "secure_url": p.url})
        return render_template("view_private_album.html", album_name=album_name, images=images)
    except Exception as e:
        return f"Error loading private album: {str(e)}"

@app.route("/delete_private_images", methods=["POST"])
@login_required
def delete_private_images():
    public_ids = request.form.getlist("public_ids")
    album_name = request.form.get("album_name")
    if not public_ids:
        flash("No images selected for deletion.", "warning")
        return redirect(url_for("view_private_album", album_name=album_name))

    try:
        to_delete_urls = [pid for pid in public_ids if pid.startswith("http")]
        to_delete_public_ids = [pid for pid in public_ids if not pid.startswith("http")]

        for url in to_delete_urls:
            path = supabase_path_from_public_url(url)
            if path and supabase:
                try:
                    supabase.storage.from_(SUPABASE_BUCKET).remove([path])
                except Exception as e:
                    print("Supabase remove failed:", e)
            photo = Photo.query.filter_by(url=url).first()
            if photo:
                db.session.delete(photo)

        if to_delete_public_ids:
            try:
                cloudinary.api.delete_resources(to_delete_public_ids)
            except Exception as e:
                print("Cloudinary delete failed:", e)
            for pid in to_delete_public_ids:
                Photo.query.filter(Photo.url.contains(pid)).delete(synchronize_session=False)

        db.session.commit()
        flash(f"Deleted {len(public_ids)} images successfully.", "success")
    except Exception as e:
        db.session.rollback()
        flash(f"Delete failed: {str(e)}", "error")

    return redirect(url_for("view_private_album", album_name=album_name))

# --------------------------
# Cloudinary 签名（前端直传时使用）
# --------------------------
@app.route("/cloudinary-sign", methods=["POST"])
@login_required
def cloudinary_sign():
    data = request.get_json(force=True) or {}
    folder = data.get("folder", "").strip()
    timestamp = int(time.time())

    params_to_sign = {"timestamp": timestamp}
    if folder:
        params_to_sign["folder"] = folder

    signature = api_sign_request(params_to_sign, cloudinary.config().api_secret)

    return {
        "timestamp": timestamp,
        "signature": signature,
        "api_key": cloudinary.config().api_key,
        "cloud_name": cloudinary.config().cloud_name,
    }

# --------------------------
# 保存上传后的图片记录到 DB（前端调用）
# --------------------------
@app.route("/save_photo", methods=["POST"])
@login_required
def save_photo():
    data = request.get_json() or {}
    album = data.get("album")
    url = data.get("url")
    is_private = bool(data.get("private"))

    if not album or not url:
        return jsonify({"success": False, "error": "缺少 album 或 url"}), 400

    try:
        exists = Photo.query.filter_by(url=url).first()
        if exists:
            return jsonify({"success": True, "message": "already_exists"})

        new_photo = Photo(
            album=album,
            url=url,
            created_at=datetime.utcnow(),
            is_private=is_private
        )
        db.session.add(new_photo)
        db.session.commit()
        return jsonify({"success": True})
    except Exception as e:
        db.session.rollback()
        return jsonify({"success": False, "error": str(e)}), 500

# --------------------------
# 上传调试页面
# --------------------------
@app.route("/upload_debug", methods=["GET", "POST"])
def upload_debug():
    if request.method == "POST":
        files = request.files.getlist("file")
        results = []

        for file in files:
            if file and file.filename:
                try:
                    file.stream.seek(0, io.SEEK_END)
                    size = file.stream.tell()
                    file.stream.seek(0)
                    results.append(f"{file.filename}: {size/1024/1024:.2f} MB (Flask 收到)")
                except Exception as e:
                    results.append(f"{file.filename}: ❌ 读取大小失败 ({e})")

        return "<br>".join(results)

    return '''
        <form method="POST" enctype="multipart/form-data">
            <input type="file" name="file" multiple>
            <button type="submit">上传测试</button>
        </form>
    '''

# --------------------------
# 启动
# --------------------------
if __name__ == "__main__":
    app.run(debug=True)

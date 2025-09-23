# main.py  —— 可直接替换（覆盖你当前文件）
import os
import re
import io
import uuid
from datetime import datetime
from functools import wraps
from urllib.parse import urlparse, unquote

from flask import Flask, render_template, request, redirect, url_for, session, flash, jsonify
from werkzeug.utils import secure_filename
from PIL import Image, ExifTags, UnidentifiedImageError

from flask_sqlalchemy import SQLAlchemy
from flask_migrate import Migrate

# optional supabase client (if you have it installed and env vars set)
try:
    from supabase import create_client, Client as SupabaseClient
except Exception:
    create_client = None
    SupabaseClient = None

# Cloudinary is still used for Story (unchanged)
import cloudinary
import cloudinary.uploader
import cloudinary.api
from cloudinary.utils import api_sign_request

# (optional helper used in test-db route)
from sqlalchemy import text

# --------------------------
# Flask init
# --------------------------
app = Flask(__name__)
app.secret_key = os.getenv('FLASK_SECRET', 'xia0720_secret')

# --------------------------
# Cloudinary config (left for Story features)
# --------------------------
cloudinary.config(
    cloud_name=os.getenv('CLOUDINARY_CLOUD_NAME', 'dqmez4f6x'),
    api_key=os.getenv('CLOUDINARY_API_KEY', '964243141587263'),
    api_secret=os.getenv('CLOUDINARY_API_SECRET', 'nh-yjA_3rQIw7wNgqyHQo4gwCIY')
)

# --------------------------
# Supabase config (optional). If not present, we fallback to local storage.
# --------------------------
SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_KEY = os.getenv("SUPABASE_KEY")
SUPABASE_BUCKET = os.getenv("SUPABASE_BUCKET", "photos")
use_supabase = False
supabase = None
if SUPABASE_URL and SUPABASE_KEY and create_client:
    try:
        supabase = create_client(SUPABASE_URL, SUPABASE_KEY)
        use_supabase = True
    except Exception as e:
        app.logger.warning("Supabase client init failed: %s", e)
        supabase = None
        use_supabase = False

# --------------------------
# DB config (same as original)
# --------------------------
database_url = os.getenv("DATABASE_URL")
if database_url:
    app.config['SQLALCHEMY_DATABASE_URI'] = database_url
else:
    # use absolute path to avoid "unable to open database file" errors
    db_path = os.path.join(os.path.abspath(os.path.dirname(__file__)), "instance", "app.db")
    app.config['SQLALCHEMY_DATABASE_URI'] = f"sqlite:///{db_path}"

app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
db = SQLAlchemy(app)
migrate = Migrate(app, db)

# --------------------------
# Models (kept compatible)
# --------------------------
class Photo(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    album = db.Column(db.String(128), nullable=False)
    url = db.Column(db.String(512), nullable=False, unique=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    is_private = db.Column(db.Boolean, default=False)

class Story(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    text = db.Column(db.Text, nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    images = db.relationship("StoryImage", backref="story", cascade="all, delete-orphan")

class StoryImage(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    image_url = db.Column(db.String(500), nullable=False)
    story_id = db.Column(db.Integer, db.ForeignKey("story.id"), nullable=False)

class Album(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(255), unique=True, nullable=False)
    drive_folder_id = db.Column(db.String(255), nullable=True)  # Google Drive 文件夹 ID

# ensure static upload folder exists (fallback)
LOCAL_UPLOAD_DIR = os.path.join(app.root_path, "static", "uploads")
os.makedirs(LOCAL_UPLOAD_DIR, exist_ok=True)

# create tables if not exist
with app.app_context():
    pass  # 不要直接创建表，交给 Flask-Migrate 管理
    
# --------------------------
# Helper: inject logged_in into all templates
# --------------------------
@app.context_processor
def inject_logged_in():
    # Use the same session key your login sets. Here we use 'logged_in' as earlier code did.
    return dict(logged_in=bool(session.get("logged_in", False)))

# --------------------------
# Utils: image compress
# --------------------------
MAX_UPLOAD_BYTES = 3 * 1024 * 1024  # try to compress to <= 3MB

def compress_image_bytes(input_bytes, target_bytes=MAX_UPLOAD_BYTES, max_dim=3000):
    """
    Return BytesIO containing JPEG bytes compressed to be <= target_bytes if possible.
    """
    try:
        img = Image.open(io.BytesIO(input_bytes))
    except UnidentifiedImageError:
        # not an image -> return original
        return io.BytesIO(input_bytes)

    # fix orientation if any
    try:
        exif = img._getexif()
        if exif:
            orientation_key = next((k for k, v in ExifTags.TAGS.items() if v == "Orientation"), None)
            if orientation_key:
                o = exif.get(orientation_key)
                if o == 3:
                    img = img.rotate(180, expand=True)
                elif o == 6:
                    img = img.rotate(270, expand=True)
                elif o == 8:
                    img = img.rotate(90, expand=True)
    except Exception:
        pass

    # resize if too large
    w, h = img.size
    if max(w, h) > max_dim:
        if w >= h:
            new_w = max_dim
            new_h = int(h * max_dim / w)
        else:
            new_h = max_dim
            new_w = int(w * max_dim / h)
        img = img.resize((new_w, new_h), Image.LANCZOS)

    # compress loop
    out = io.BytesIO()
    quality = 85
    img.convert("RGB").save(out, format="JPEG", quality=quality, optimize=True)
    while out.tell() > target_bytes and quality > 30:
        quality -= 10
        out.seek(0); out.truncate(0)
        img.convert("RGB").save(out, format="JPEG", quality=quality, optimize=True)

    out.seek(0)
    return out

def safe_filename(name):
    base = name.rsplit('.', 1)[0]
    safe = re.sub(r'[^a-zA-Z0-9_-]', '_', base).strip('_') or str(uuid.uuid4())
    return safe + ".jpg"

def login_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if not session.get("logged_in"):
            flash("Please log in first.", "warning")
            return redirect(url_for("login"))
        return f(*args, **kwargs)
    return decorated_function

def compress_image_file(tmp_path, output_dir=LOCAL_UPLOAD_DIR, max_size=(1920,1920), quality=75):
    """
    压缩图片文件并保存到 output_dir，返回压缩后的文件路径
    ⚡ 使用本地文件避免一次性大文件占用内存
    """
    import os
    from PIL import Image, ExifTags, UnidentifiedImageError
    import shutil

    try:
        img = Image.open(tmp_path)
    except UnidentifiedImageError:
        # 非图片 -> 直接复制原文件
        output_path = os.path.join(output_dir, os.path.basename(tmp_path))
        shutil.copy(tmp_path, output_path)
        return output_path

    # 修正方向
    try:
        exif = img._getexif()
        if exif:
            orientation_key = next((k for k,v in ExifTags.TAGS.items() if v=="Orientation"), None)
            if orientation_key:
                o = exif.get(orientation_key)
                if o==3:
                    img = img.rotate(180, expand=True)
                elif o==6:
                    img = img.rotate(270, expand=True)
                elif o==8:
                    img = img.rotate(90, expand=True)
    except Exception:
        pass

    # 限制尺寸
    img.thumbnail(max_size, Image.LANCZOS)

    # 输出路径
    output_path = os.path.join(output_dir, f"compressed_{os.path.basename(tmp_path)}")

    # 保存 JPEG
    if img.mode != "RGB":
        img = img.convert("RGB")
    img.save(output_path, format="JPEG", quality=quality, optimize=True)

    return output_path
# --------------------------
# Routes: index / static pages
# --------------------------
@app.route("/")
def index():
    return render_template("index.html")

@app.route("/about")
def about():
    return render_template("about.html")

# --------------------------
# Albums list (reads from DB)
# --------------------------
@app.route("/album")
def albums():
    try:
        # get distinct album names and one cover each (min created_at)
        rows = db.session.query(Photo.album, Photo.url).filter_by(is_private=False).order_by(Photo.album, Photo.created_at).all()
        # build a dict of first url per album
        album_map = {}
        for album, url in rows:
            if album not in album_map:
                album_map[album] = url
        albums_list = [{"name": name, "cover": album_map.get(name)} for name in sorted(album_map.keys())]
        return render_template("album.html", albums=albums_list)
    except Exception as e:
        app.logger.exception("Failed to load albums")
        return f"Error loading albums: {e}", 500

# --------------------------
# View album (public)
# --------------------------
@app.route("/album/<album_name>")
def view_album(album_name):
    try:
        # 获取相册的所有公开照片
        photos = Photo.query.filter_by(album=album_name, is_private=False).order_by(Photo.created_at.desc()).all()
        images = []
        for p in photos:
            images.append({
                "id": p.id,
                "url": p.url,
                "source": p.url,   # 兼容老模板
                "created_at": p.created_at
            })

        # 获取相册对象，拿 drive_folder_id
        album_obj = Album.query.filter_by(name=album_name).first()
        drive_link = None
        if album_obj and album_obj.drive_folder_id:
            drive_link = f"https://drive.google.com/drive/folders/{album_obj.drive_folder_id}"

        return render_template(
            "view_album.html",
            album_name=album_name,
            images=images,
            drive_link=drive_link
        )
    except Exception as e:
        app.logger.exception("view_album failed")
        return f"Error loading album: {e}", 500
        
# --------------------------
# Delete endpoints (handle id or url)
# --------------------------
@app.route("/delete_images", methods=["POST"])
@login_required
def delete_images():
    ids = request.form.getlist("photo_ids") or request.form.getlist("to_delete") or request.form.getlist("public_ids")
    album_name = request.form.get("album_name") or request.form.get("album")

    if not ids:
        flash("No images selected for deletion.", "warning")
        return redirect(url_for("view_album", album_name=album_name) if album_name else url_for("albums"))

    deleted_db = 0
    deleted_local_files = 0

    for ident in ids:
        p = None
        # 按 id 查询
        try:
            pid = int(ident)
            p = Photo.query.get(pid)
        except Exception:
            pass
        # 若没找到，再按 URL 查询
        if not p:
            p = Photo.query.filter_by(url=ident).first()
        if not p:
            continue

        # 删除本地文件
        filename = None
        try:
            parsed = urlparse(p.url)
            path = unquote(parsed.path or "")
            filename = os.path.basename(path) if path else None
        except Exception:
            filename = None

        if filename:
            local_path = os.path.join(LOCAL_UPLOAD_DIR, filename)
            if os.path.exists(local_path):
                try:
                    os.remove(local_path)
                    deleted_local_files += 1
                except Exception as e:
                    app.logger.debug("Failed to delete local file %s: %s", local_path, e)

        # 删除 Supabase 文件
        if use_supabase and supabase and filename and album_name:
            possible_paths = [
                f"{album_name}/{filename}",
                f"private/{album_name}/{filename}",
                filename
            ]
            for _path in possible_paths:
                try:
                    supabase.storage.from_(SUPABASE_BUCKET).remove([_path])
                except Exception as e:
                    app.logger.debug("Supabase remove(%s) failed: %s", _path, e)

        # 删除数据库记录
        try:
            db.session.delete(p)
            db.session.commit()
            deleted_db += 1
        except Exception as e:
            app.logger.exception("Failed to delete Photo DB record %s: %s", p.id if p else "?", e)
            db.session.rollback()

    flash(f"Deleted {deleted_db} images, {deleted_local_files} local files removed.", "success")
    return redirect(url_for("view_album", album_name=album_name) if album_name else url_for("albums"))
    

@app.route("/delete_private_images", methods=["POST"])
def delete_private_images():
    if not session.get("logged_in"):
        flash("Login required to delete images.", "warning")
        return redirect(url_for("login"))

    ids = request.form.getlist("public_ids") or request.form.getlist("photo_ids") or []
    album_name = request.form.get("album_name")

    deleted = 0
    for ident in ids:
        try:
            pid = int(ident)
            p = Photo.query.get(pid)
            if p:
                db.session.delete(p); db.session.commit(); deleted += 1; continue
        except Exception:
            pass
        p = Photo.query.filter_by(url=ident).first()
        if p:
            db.session.delete(p); db.session.commit(); deleted += 1; continue

    flash(f"Deleted {deleted} images.", "success")
    return redirect(url_for("view_private_album", album_name=album_name) if album_name else url_for("private_space"))

# --------------------------
# Delete entire album (新加)
# --------------------------
@app.route("/delete_album/<album_name>", methods=["POST"])
@login_required
def delete_album(album_name):
    """
    删除整个相册：
      - 删除 Supabase 文件
      - 删除本地文件
      - 删除 Photo 数据库记录
      - 删除 Album 数据库记录
    """
    try:
        photos = Photo.query.filter_by(album=album_name).all()
        deleted_db = 0
        deleted_local_files = 0

        for p in photos:
            filename = None
            try:
                parsed = urlparse(p.url)
                path = unquote(parsed.path or "")
                filename = os.path.basename(path) if path else None
            except Exception:
                pass

            # 删除 Supabase 文件
            if use_supabase and supabase and filename:
                possible_paths = [
                    f"{album_name}/{filename}",
                    f"private/{album_name}/{filename}",
                    filename
                ]
                for _path in possible_paths:
                    try:
                        supabase.storage.from_(SUPABASE_BUCKET).remove([_path])
                    except Exception as e:
                        app.logger.debug("Supabase remove(%s) failed: %s", _path, e)

            # 删除本地文件
            if filename:
                local_path = os.path.join(LOCAL_UPLOAD_DIR, filename)
                if os.path.exists(local_path):
                    try:
                        os.remove(local_path)
                        deleted_local_files += 1
                    except Exception as e:
                        app.logger.debug("Failed to delete local file %s: %s", local_path, e)

            # 删除数据库记录
            try:
                db.session.delete(p)
                db.session.commit()
                deleted_db += 1
            except Exception as e:
                app.logger.exception("Failed to delete Photo DB record %s: %s", p.id if p else "?", e)
                db.session.rollback()

        # 删除 Album 记录
        album_obj = Album.query.filter_by(name=album_name).first()
        if album_obj:
            try:
                db.session.delete(album_obj)
                db.session.commit()
            except Exception:
                db.session.rollback()

        flash(f"Deleted album '{album_name}': {deleted_db} photo records removed, {deleted_local_files} local files removed.", "success")
        return redirect(url_for("albums"))

    except Exception as e:
        app.logger.exception("delete_album failed")
        flash(f"Failed to delete album '{album_name}': {e}", "danger")
        return redirect(url_for("albums"))

# --------------------------
# Story 列表
# --------------------------
@app.route("/story_list")
def story_list():
    stories = Story.query.order_by(Story.created_at.desc()).all()

    for story in stories:
        for img in story.images:
            # 如果 URL 是空或者不是 Cloudinary URL，就尝试修复
            if not img.image_url or not img.image_url.startswith("https://res.cloudinary.com/dpr0pl2tf/"):
                try:
                    # 假设旧图片 filename 在数据库 image_url 中保存
                    filename = img.image_url.split("/")[-1]  # 旧路径最后部分
                    public_id = filename.rsplit(".", 1)[0]   # 去掉扩展名
                    # 假设旧 Story 图片都在 Cloudinary 文件夹 story/
                    new_url, _ = cloudinary.utils.cloudinary_url(f"story/{public_id}")
                    img.image_url = new_url
                except Exception as e:
                    print(f"⚠️ 修复旧 Story 图片失败: {img.image_url} -> {e}")

    # 仅渲染页面，不修改数据库
    return render_template("story_list.html", stories=stories, logged_in=session.get("logged_in", False))

# --------------------------
# Story 详情
# --------------------------
@app.route("/story/<int:story_id>")
def story_detail(story_id):
    story = Story.query.get_or_404(story_id)
    return render_template("story_detail.html", story=story)

# --------------------------
# 上传新 Story（仅登录）
# --------------------------
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
                    # 压缩大文件 > 9.5MB
                    file.stream.seek(0, 2)  # 移动到末尾
                    size = file.stream.tell()
                    file.stream.seek(0)

                    if size > 9.5 * 1024 * 1024:  # 超过 9.5MB
                        img = Image.open(file.stream)
                        img = img.convert("RGB")
                        buf = io.BytesIO()
                        img.save(buf, format="JPEG", quality=85, optimize=True)
                        buf.seek(0)
                        upload_result = cloudinary.uploader.upload(
                            buf,
                            folder="stories"
                        )
                    else:
                        upload_result = cloudinary.uploader.upload(
                            file,
                            folder="stories"
                        )

                    img_url = upload_result.get("secure_url")
                    if img_url:
                        db.session.add(StoryImage(image_url=img_url, story=new_story))
                except Exception as e:
                    print(f"⚠️ 上传故事图片失败: {e}")
                    flash(f"One image failed to upload: {file.filename}", "error")

        db.session.commit()
        flash("Story uploaded successfully!", "success")
        return redirect(url_for("story_list"))

    return render_template("upload_story.html")

# --------------------------
# 编辑 Story（仅登录）
# --------------------------
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

        # 删除选中的旧图
        delete_image_ids = request.form.get("delete_images", "")
        if delete_image_ids:
            for img_id in delete_image_ids.split(","):
                img = StoryImage.query.get(int(img_id))
                if img:
                    db.session.delete(img)

        # 上传新图
        files = request.files.getlist("story_images")
        for file in files:
            if file and file.filename:
                try:
                    # 检查文件大小
                    file.stream.seek(0, 2)  # 移动到末尾
                    size = file.stream.tell()
                    file.stream.seek(0)  # 回到开头

                    if size > 9.5 * 1024 * 1024:  # 大于9.5MB，压缩
                        img = Image.open(file.stream)
                        img = img.convert("RGB")
                        buf = io.BytesIO()
                        img.save(buf, format="JPEG", quality=85, optimize=True)
                        buf.seek(0)
                        upload_result = cloudinary.uploader.upload(buf, folder="stories")
                    else:
                        upload_result = cloudinary.uploader.upload(file, folder="stories")

                    img_url = upload_result.get("secure_url")
                    if img_url:
                        db.session.add(StoryImage(image_url=img_url, story=story))
                except Exception as e:
                    print(f"⚠️ 编辑 Story 上传图片失败: {e}")
                    flash(f"Image {file.filename} failed to upload", "error")

        db.session.commit()
        flash("Story updated", "success")
        return redirect(url_for("story_detail", story_id=story.id))

    return render_template("edit_story.html", story=story)
# --------------------------
# 删除 Story（仅登录）
# --------------------------
@app.route("/delete_story/<int:story_id>", methods=["POST"])
@login_required
def delete_story(story_id):
    story = Story.query.get_or_404(story_id)
    db.session.delete(story)
    db.session.commit()
    flash("Story deleted.", "info")
    return redirect(url_for("story_list"))

# --------------------------
# Upload (public album) - accepts multipart/form-data
# --------------------------
@app.route("/upload", methods=["GET", "POST"])
def upload():
    if request.method == "GET":
        rows = db.session.query(Album.name).all()
        album_names = [r[0] for r in rows]
        return render_template(
            "upload.html",
            album_names=album_names,
            last_album=session.get("last_album", "")
        )

    # POST
    try:
        album_name = (request.form.get("album") or request.form.get("new_album") or "").strip()
        if not album_name:
            return jsonify({"success": False, "error": "album name required"}), 400

        drive_folder_id = request.form.get("drive_folder_id", "").strip()

        album_obj = Album.query.filter_by(name=album_name).first()
        if not album_obj:
            album_obj = Album(name=album_name, drive_folder_id=drive_folder_id or None)
            db.session.add(album_obj)
            db.session.commit()
        else:
            if drive_folder_id:
                album_obj.drive_folder_id = drive_folder_id
                db.session.commit()

        files = request.files.getlist("photo")
        if not files:
            return jsonify({"success": False, "error": "no files"}), 400

        uploaded_urls = []

        for f in files:
            if not f or not f.filename:
                continue

            filename = f"{uuid.uuid4().hex}_{secure_filename(f.filename)}"

            # ⚡ 流式写入临时文件，避免占用内存
            tmp_path = os.path.join("/tmp", filename)
            with open(tmp_path, "wb") as tmp_file:
                for chunk in f.stream:
                    tmp_file.write(chunk)

            # 使用 compress_image_file(tmp_path) 压缩到本地临时文件
            compressed_path = compress_image_file(tmp_path)  # 你自己实现的函数，返回压缩后的文件路径
            file_bytes = None
            with open(compressed_path, "rb") as buf:
                file_bytes = buf.read()  # 压缩后读取内容

            public_url = None
            if use_supabase and supabase:
                try:
                    path = f"{album_name}/{filename}"
                    supabase.storage.from_(SUPABASE_BUCKET).upload(
                        path,
                        file_bytes,
                        {"content-type": f.mimetype or "application/octet-stream", "upsert": "true"}
                    )
                    pub = supabase.storage.from_(SUPABASE_BUCKET).get_public_url(path)
                    if isinstance(pub, dict):
                        public_url = pub.get("publicURL") or pub.get("public_url") or pub.get("publicUrl")
                    elif isinstance(pub, str):
                        public_url = pub
                except Exception as e:
                    app.logger.exception("Supabase upload failed, fallback to local: %s", e)
                    public_url = None

            # fallback 本地
            if not public_url:
                local_path = os.path.join(LOCAL_UPLOAD_DIR, filename)
                os.replace(compressed_path, local_path)
                public_url = url_for("static", filename=f"uploads/{filename}", _external=True)
            else:
                os.remove(compressed_path)

            # 保存到数据库
            new_photo = Photo(album=album_name, url=public_url, is_private=False)
            db.session.add(new_photo)
            db.session.commit()

            # ⚡ 保留 drive 链接
            drive_link = f"https://drive.google.com/drive/folders/{album_obj.drive_folder_id}" if album_obj.drive_folder_id else None
            uploaded_urls.append({"photo_url": public_url, "drive_link": drive_link})

        session["last_album"] = album_name
        return jsonify({"success": True, "uploads": uploaded_urls})

    except Exception as e:
        app.logger.exception("Upload failed")
        return jsonify({"success": False, "error": str(e)}), 500
# --------------------------
# Upload private (logged-in required)
# --------------------------
@app.route("/upload_private", methods=["POST"])
def upload_private():
    if not session.get("logged_in"):
        return jsonify({"success": False, "error": "login required"}), 401

    try:
        album = (request.form.get("album") or request.form.get("new_album") or "").strip()
        if not album:
            return jsonify({"success": False, "error": "album name required"}), 400

        files = request.files.getlist("photo")
        if not files:
            return jsonify({"success": False, "error": "no files"}), 400

        uploaded_urls = []
        for f in files:
            if not f or not f.filename:
                continue
            raw = f.read()
            buf = compress_image_bytes(raw)   # BytesIO
            file_bytes = buf.getvalue()       # ✅ 转成 bytes
            filename = safe_filename(f.filename)

            public_url = None
            if use_supabase and supabase:
                try:
                    path = f"private/{album}/{filename}"
                    res = supabase.storage.from_(SUPABASE_BUCKET).upload(path, file_bytes, {"upsert": True})
                    pub = supabase.storage.from_(SUPABASE_BUCKET).get_public_url(path)
                    if isinstance(pub, dict):
                        public_url = pub.get("publicURL") or pub.get("public_url") or pub.get("publicUrl")
                    elif isinstance(pub, str):
                        public_url = pub
                except Exception as e:
                    app.logger.exception("Supabase private upload failed, fallback to local: %s", e)
                    public_url = None

            if not public_url:
                local_path = os.path.join(LOCAL_UPLOAD_DIR, filename)
                with open(local_path, "wb") as out:
                    out.write(file_bytes)
                public_url = url_for('static', filename=f"uploads/{filename}", _external=True)

            new_photo = Photo(album=album, url=public_url, is_private=True)
            db.session.add(new_photo)
            db.session.commit()
            uploaded_urls.append(public_url)

        return jsonify({"success": True, "urls": uploaded_urls, "album": album})

    except Exception as e:
        app.logger.exception("upload_private failed")
        return jsonify({"success": False, "error": str(e)}), 500
        
# --------------------------
# Login / logout
# --------------------------
@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        username = request.form.get("username", "")
        password = request.form.get("password", "")
        # keep your original credential check
        if username == "xia0720" and password == "qq123456":
            session["logged_in"] = True
            flash("Logged in.", "success")
            next_url = request.args.get("next")
            return redirect(next_url or url_for("story_list"))
        else:
            flash("Invalid credentials.", "danger")
            return redirect(url_for("login"))
    return render_template("login.html")


@app.route("/logout")
def logout():
    session.pop("logged_in", None)
    flash("Logged out.", "info")
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
# Private-space index (shows private albums)
# --------------------------
@app.route("/private_space")
def private_space():
    # protect page - only allow logged_in; but if you want guests to see list, remove guard
    if not session.get("logged_in"):
        return redirect(url_for("login", next=request.path))

    try:
        rows = db.session.query(Photo.album, Photo.url).filter_by(is_private=True).order_by(Photo.album, Photo.created_at).all()
        album_map = {}
        for album, url in rows:
            if album not in album_map:
                album_map[album] = url
        album_names = sorted(album_map.keys())
        album_covers = {k: v for k, v in album_map.items()}
        return render_template("private_album.html", album_names=album_names, album_covers=album_covers, last_album=session.get("last_private_album", ""))
    except Exception as e:
        app.logger.exception("private_space failed")
        return f"Error loading private space: {e}", 500


# --------------------------
# View private album (only logged in)
# --------------------------
@app.route("/private_space/<album_name>")
def view_private_album(album_name):
    if not session.get("logged_in"):
        return redirect(url_for("login", next=request.path))
    try:
        photos = Photo.query.filter_by(album=album_name, is_private=True).order_by(Photo.created_at.desc()).all()
        images = []
        for p in photos:
            images.append({
                "id": p.id,
                "url": p.url,
                "secure_url": p.url,   # for compatibility with templates expecting secure_url
                "public_id": str(p.id)
            })
        return render_template("view_private_album.html", album_name=album_name, images=images)
    except Exception as e:
        app.logger.exception("view_private_album failed")
        return f"Error loading private album: {e}", 500


# --------------------------
# Save photo endpoint (compatibility - JSON)
# --------------------------
@app.route("/save_photo", methods=["POST"])
def save_photo():
    try:
        data = None
        if request.is_json:
            data = request.get_json()
        else:
            # try form
            data = request.form.to_dict()

        album = data.get("album") or data.get("album_name")
        url = data.get("url") or data.get("file_url")
        is_private = data.get("private") in (True, "true", "1", "on")

        if not album or not url:
            return jsonify({"success": False, "error": "missing album or url"}), 400

        # prevent duplicates
        exists = Photo.query.filter_by(url=url).first()
        if exists:
            return jsonify({"success": True, "message": "already_exists"})

        new_photo = Photo(album=album, url=url, is_private=is_private)
        db.session.add(new_photo)
        db.session.commit()
        return jsonify({"success": True})
    except Exception as e:
        app.logger.exception("save_photo failed")
        return jsonify({"success": False, "error": str(e)}), 500
# --------------------------
# 启动
# --------------------------
if __name__ == "__main__":
    app.run(debug=True)

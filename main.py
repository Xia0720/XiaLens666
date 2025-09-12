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
from sqlalchemy.pool import NullPool
import re, uuid
from models import db, Photo

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

# main.py（靠近 cloudinary.config(...) 的地方）
MAIN_ALBUM_FOLDER = os.getenv("MAIN_ALBUM_FOLDER", "albums")  # 若不想主文件夹，设置为空字符串 ""
MAX_CLOUDINARY_SIZE = 10 * 1024 * 1024  # 10MB

# --------------------------
# 数据库配置
# --------------------------
database_url = os.getenv("DATABASE_URL")  # Render / Supabase
if database_url:
    app.config['SQLALCHEMY_DATABASE_URI'] = database_url
else:
    app.config['SQLALCHEMY_DATABASE_URI'] = "sqlite:///stories.db"

app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
app.config['SQLALCHEMY_ENGINE_OPTIONS'] = {
    "poolclass": NullPool
}

db = SQLAlchemy(app)
migrate = Migrate(app, db)

# 保证请求结束后释放 session
@app.teardown_appcontext
def shutdown_session(exception=None):
    db.session.remove()

# --------------------------
# 模板全局变量
# --------------------------
@app.context_processor
def inject_logged_in():
    return dict(logged_in=session.get("logged_in", False))

# ---------- 工具函数：自动修正图片方向 ----------
def fix_image_orientation(file):
    img = Image.open(file)
    try:
        for orientation in ExifTags.TAGS.keys():
            if ExifTags.TAGS[orientation] == "Orientation":
                break

        exif = img._getexif()
        if exif is not None:
            orientation_value = exif.get(orientation)
            if orientation_value == 3:
                img = img.rotate(180, expand=True)
            elif orientation_value == 6:
                img = img.rotate(270, expand=True)
            elif orientation_value == 8:
                img = img.rotate(90, expand=True)
    except Exception:
        pass  # 没有EXIF就跳过

    return img

# --------------------------
# 登录保护装饰器
# --------------------------
def login_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if not session.get("logged_in"):
            return redirect(url_for("login", next=request.path))
        return f(*args, **kwargs)
    return decorated


# 新增 Photo 数据模型
# --------------------------
class Photo(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    album = db.Column(db.String(128), nullable=False)   # 保持原来 128
    url = db.Column(db.String(512), nullable=False, unique=True)  # 保持原来 512，加 unique
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    is_private = db.Column(db.Boolean, default=False)   # 新增字段
# --------------------------
# 数据模型
# --------------------------
class Story(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    text = db.Column(db.Text, nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    images = db.relationship("StoryImage", backref="story", cascade="all, delete-orphan")

class StoryImage(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    image_url = db.Column(db.String(500), nullable=False)
    story_id = db.Column(db.Integer, db.ForeignKey("story.id"), nullable=False)

# 确保 instance 文件夹存在
if not os.path.exists('instance'):
    os.makedirs('instance')

# 自动创建表
with app.app_context():
    db.create_all()

# --------------------------
# 首页和静态页面
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
# 相册列表
# --------------------------
@app.route("/album")
def albums():
    try:
        albums_list = []
        main = (MAIN_ALBUM_FOLDER or "").strip('/')
        if not main:
            return "MAIN_ALBUM_FOLDER 未设置"

        # ✅ 获取一级子文件夹（真正的相册列表）
        folders = cloudinary.api.subfolders(main)
        album_names = [f["name"] for f in folders.get("folders", [])]

        # ✅ 每个相册获取 1 张照片作为封面
        for album_name in sorted(album_names):
            r = cloudinary.api.resources(
                type="upload",
                prefix=f"{main}/{album_name}/",
                max_results=1
            )
            if not r.get('resources'):
                continue
            cover_url = r['resources'][0]['secure_url']
            albums_list.append({'name': album_name, 'cover': cover_url})

        return render_template("album.html", albums=albums_list)

    except Exception as e:
        print("ERROR in /album:", str(e))
        return f"Error fetching albums: {str(e)}"

# --------------------------
# Album 内容页
# --------------------------
@app.route("/album/<album_name>", methods=["GET", "POST"])
def view_album(album_name):
    try:
        main = (MAIN_ALBUM_FOLDER or "").strip('/')
        prefix = f"{main}/{album_name}" if main else album_name

        resources = cloudinary.api.resources(
            type="upload",
            prefix=prefix + "/",   # ✅ 加上斜杠，确保只匹配这个文件夹
            max_results=500
        )

        images = [
            {"public_id": img["public_id"], "secure_url": img["secure_url"]}
            for img in resources.get("resources", [])
            if img.get("public_id", "").startswith(prefix + "/")  # ✅ 二次过滤
        ]

        logged_in = session.get("logged_in", False)
        return render_template("view_album.html", album_name=album_name, images=images, logged_in=logged_in)

    except Exception as e:
        return f"Error loading album: {str(e)}"

# --------------------------
# 删除图片（仅登录）
# --------------------------
@app.route("/delete_images", methods=["POST"])
def delete_images():
    public_ids = request.form.getlist("public_ids")
    album_name = request.form.get("album_name")
    if not public_ids:
        flash("No images selected for deletion.", "warning")
        return redirect(url_for("view_album", album_name=album_name))
    try:
        cloudinary.api.delete_resources(public_ids)
        flash(f"Deleted {len(public_ids)} images successfully.", "success")
    except Exception as e:
        flash(f"Delete failed: {str(e)}", "error")
    return redirect(url_for("view_album", album_name=album_name))

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
# 上传图片到 Cloudinary album（仅登录）
# --------------------------
@app.route("/upload", methods=["GET", "POST"])
def upload():
    if request.method == "POST":
        # 前端传过来的相册名（new 或已有）
        album_name = request.form.get("album") or request.form.get("new_album")
        files = request.files.getlist("photo")  # 注意 input name="photo"

        uploaded_urls = []

        for file in files:
            if file and file.filename:
                try:
                    folder_path = f"{MAIN_ALBUM_FOLDER}/{album_name}" if MAIN_ALBUM_FOLDER else album_name
                    result = cloudinary.uploader.upload(
                        file,
                        folder=folder_path,
                        public_id=file.filename.rsplit('.', 1)[0]  # 保留文件名去掉扩展名
                    )
                    uploaded_urls.append(result["secure_url"])
                except Exception as e:
                    print(f"❌ 上传失败 {file.filename}: {e}")

        # 返回 JSON 给前端处理
        return jsonify({"success": True, "urls": uploaded_urls})

    # ========== GET 请求：获取所有已创建相册 ==========
    album_names_set = set()
    main_prefix = (MAIN_ALBUM_FOLDER or "").strip('/')

    try:
        # 分页获取所有资源，防止 max_results 限制
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
                    # 去掉主文件夹前缀
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
        print(f"⚠️ 获取相册失败: {e}")
        album_names = []

    return render_template(
        "upload.html",
        album_names=album_names,
        MAIN_ALBUM_FOLDER=MAIN_ALBUM_FOLDER,
        last_album=""  # 可选：记录上次上传相册
    )

# --------------------------
# 私密空间上传（仅登录）
# --------------------------
# --------------------------
# 私密空间上传（严格私有化，仅登录）
# --------------------------
@app.route("/upload_private", methods=["POST"])
@login_required
def upload_private():
    album_name = request.form.get("album")
    if album_name == "new":
        album_name = (request.form.get("new_album") or "").strip()
        if not album_name:
            return jsonify({"success": False, "error": "相册名不能为空"}), 400

    files = request.files.getlist("photo")
    if not files or all(f.filename == '' for f in files):
        return jsonify({"success": False, "error": "请选择至少一张照片"}), 400

    uploaded_ids = []

    for file in files:
        if not file or file.filename == '':
            continue
        try:
            # ---- public_id 安全处理 ----
            base_name = file.filename.rsplit('.', 1)[0]
            safe_name = re.sub(r'[^a-zA-Z0-9_-]', '_', base_name).strip('_')
            if not safe_name:
                safe_name = str(uuid.uuid4())

            file.stream.seek(0)
            raw = file.read()
            upload_buffer = io.BytesIO(raw)

            mimetype = (file.mimetype or "").lower()

            # ---- 压缩逻辑 (保持你原来的逻辑) ----
            if len(raw) > MAX_CLOUDINARY_SIZE and mimetype.startswith("image"):
                img = Image.open(io.BytesIO(raw))
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
                except:
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

            # ---- 上传到 Cloudinary (严格私有化) ----
            folder_path = f"private/{re.sub(r'[^a-zA-Z0-9_-]', '_', album_name).strip('_')}"
            upload_buffer.seek(0)
            result = cloudinary.uploader.upload(
                upload_buffer,
                folder=folder_path,
                public_id=safe_name,
                resource_type="image",
                access_mode="authenticated",  # 🔒 严格私有
                overwrite=True
            )

            # ---- 存数据库 (只存 public_id，不存 url) ----
            new_photo = Photo(
                album=album_name,
                url=result.get("public_id"),  # ⚠️ 只存 public_id
                is_private=True,
                created_at=datetime.utcnow()
            )
            db.session.add(new_photo)
            db.session.commit()
            uploaded_ids.append(result.get("public_id"))

        except Exception as e:
            db.session.rollback()
            return jsonify({"success": False, "error": f"上传失败 {file.filename}: {e}"}), 500

    return jsonify({"success": True, "public_ids": uploaded_ids, "album": album_name})

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
    album_covers = {}  # 存放每个相册的封面 URL
    try:
        next_cursor = None
        while True:
            resources = cloudinary.api.resources(
                type="upload",
                prefix="private/",
                max_results=500,
                next_cursor=next_cursor
            )
            for res in resources.get('resources', []):
                parts = res.get('public_id', '').split('/')
                if len(parts) >= 2:
                    album = parts[1]
                    album_names_set.add(album)
                    # 如果还没存封面，就存第一张
                    if album not in album_covers:
                        album_covers[album] = res.get("secure_url")
            next_cursor = resources.get('next_cursor')
            if not next_cursor:
                break
        album_names = sorted(album_names_set)
    except Exception as e:
        print(f"⚠️ 获取私密相册失败: {e}")
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
        resources = cloudinary.api.resources(type="upload", prefix=f"private/{album_name}", max_results=500)
        images = [{"public_id": img["public_id"], "secure_url": img["secure_url"]} for img in resources["resources"]]
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
        cloudinary.api.delete_resources(public_ids)
        flash(f"Deleted {len(public_ids)} images successfully.", "success")
    except Exception as e:
        flash(f"Delete failed: {str(e)}", "error")
    return redirect(url_for("view_private_album", album_name=album_name))

@app.route("/cloudinary-sign", methods=["POST"])
@login_required
def cloudinary_sign():      # 前端直传 Cloudinary 需要签名，这里按 folder 生成一次签名（整批文件可复用）。
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
# 保存上传到数据库
# --------------------------
@app.route("/save_photo", methods=["POST"])
@login_required
def save_photo():
    data = request.get_json() or {}
    album = data.get("album")
    url = data.get("url")
    is_private = bool(data.get("private"))  # 前端传 true / false

    if not album or not url:
        return jsonify({"success": False, "error": "缺少 album 或 url"}), 400

    try:
        # 防止重复保存
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

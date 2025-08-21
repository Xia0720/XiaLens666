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
from PIL import Image, ExifTags
import io
import time
from cloudinary.utils import api_sign_request
from PIL import Image

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

# --------------------------
# 数据库配置
# --------------------------
database_url = os.getenv("DATABASE_URL")  # Render / Supabase
if database_url:
    app.config['SQLALCHEMY_DATABASE_URI'] = database_url
else:
    app.config['SQLALCHEMY_DATABASE_URI'] = "sqlite:///stories.db"

app.config['SQLALCHEMY_ENGINE_OPTIONS'] = {
    "pool_size": 5,
    "max_overflow": 0,
    "pool_timeout": 30,
    "pool_recycle": 1800,
    "pool_pre_ping": True
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
    album = db.Column(db.String(128), nullable=False)
    url = db.Column(db.String(512), nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
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
        albums = []
        main = (MAIN_ALBUM_FOLDER or "").strip('/')

        if main:
            # 列出主文件夹下的资源，然后从 public_id 中提取第二级目录名作为子相册名
            resources = cloudinary.api.resources(type="upload", prefix=f"{main}/", max_results=500)
            album_names_set = set()
            for res in resources.get('resources', []):
                parts = res.get('public_id', '').split('/')
                # public_id 形如 "albums/<album_name>/xxx.jpg"
                if len(parts) >= 2:
                    album_names_set.add(parts[1])

            # 为每个子相册取一张封面（取第一个资源）
            for album_name in sorted(album_names_set):
                r = cloudinary.api.resources(type="upload", prefix=f"{main}/{album_name}", max_results=1)
                cover_url = r.get('resources')[0].get('secure_url') if r.get('resources') else ""
                albums.append({'name': album_name, 'cover': cover_url})
        else:
            # 兼容老逻辑：列出根目录下的文件夹（不包含 private）
            folders = cloudinary.api.root_folders()
            for folder in folders.get('folders', []):
                folder_name = folder.get('name')
                if folder_name == "private":
                    continue
                resources = cloudinary.api.resources(type="upload", prefix=folder_name, max_results=1)
                cover_url = resources.get('resources')[0].get('secure_url') if resources.get('resources') else ""
                albums.append({'name': folder_name, 'cover': cover_url})

        return render_template("album.html", albums=albums)
    except Exception as e:
        return f"Error fetching albums: {str(e)}"

# --------------------------
# Album 内容页
# --------------------------
@app.route("/album/<album_name>", methods=["GET", "POST"])
def view_album(album_name):
    try:
        main = (MAIN_ALBUM_FOLDER or "").strip('/')
        prefix = f"{main}/{album_name}" if main else album_name
        resources = cloudinary.api.resources(type="upload", prefix=prefix, max_results=500)
        images = [{"public_id": img["public_id"], "secure_url": img["secure_url"]} for img in resources.get("resources", [])]
        logged_in = session.get("logged_in", False)
        return render_template("view_album.html", album_name=album_name, images=images, logged_in=logged_in)
    except Exception as e:
        return f"Error loading album: {str(e)}"

# --------------------------
# 删除图片（仅登录）
# --------------------------
@app.route("/delete_images", methods=["POST"])
@login_required
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
                upload_result = cloudinary.uploader.upload(file)
                img_url = upload_result.get("secure_url")
                if img_url:
                    db.session.add(StoryImage(image_url=img_url, story=new_story))

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

        delete_image_ids = request.form.get("delete_images", "")
        if delete_image_ids:
            for img_id in delete_image_ids.split(","):
                img = StoryImage.query.get(int(img_id))
                if img:
                    db.session.delete(img)

        files = request.files.getlist("story_images")
        for file in files:
            if file and file.filename:
                upload_result = cloudinary.uploader.upload(file)
                img_url = upload_result.get("secure_url")
                if img_url:
                    db.session.add(StoryImage(image_url=img_url, story=story))

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
        album_name = request.form["album"]
        files = request.files.getlist("file")

        for file in files:
            if file and file.filename:
                file.seek(0)
                file_bytes = file.read()
                file.seek(0)

                # 如果超过 10MB，就压缩
                if len(file_bytes) > 10 * 1024 * 1024:
                    img = Image.open(file)
                    buffer = io.BytesIO()

                    # 初始质量设定
                    quality = 85
                    while True:
                        buffer.seek(0)
                        img.save(buffer, format="JPEG", optimize=True, quality=quality)
                        size = buffer.tell()
                        if size <= 10 * 1024 * 1024 or quality <= 40:
                            break
                        quality -= 5  # 每次降低 5 直到符合要求

                    buffer.seek(0)
                    upload_file = buffer
                else:
                    upload_file = file

                # 上传到 cloudinary
                cloudinary.uploader.upload(
                    upload_file,
                    folder=f"{MAIN_ALBUM_FOLDER}/{album_name}"
                )

        return redirect(url_for("albums"))

    # 获取已有相册名
    album_names = []
    main = (MAIN_ALBUM_FOLDER or "").strip('/')
    if main:
        resources = cloudinary.api.resources(type="upload", prefix=f"{main}/", max_results=500)
        album_names_set = set()
        for res in resources.get('resources', []):
            parts = res.get('public_id', '').split('/')
            if len(parts) >= 2:
                album_names_set.add(parts[1])
        album_names = sorted(album_names_set)

    return render_template(
        "upload.html",
        album_names=album_names,
        MAIN_ALBUM_FOLDER=MAIN_ALBUM_FOLDER
    )
# --------------------------
# 私密空间上传（仅登录）
# --------------------------
@app.route("/upload_private", methods=["GET", "POST"])
@login_required
def upload_private():
    try:
        resources = cloudinary.api.resources(type="upload", prefix="private", max_results=500)
        album_names_set = set()
        for res in resources['resources']:
            parts = res['public_id'].split('/')
            if len(parts) >= 2:
                album_names_set.add(parts[1])
        album_names = list(album_names_set)
    except Exception:
        album_names = []

    if request.method == "POST":
        photos = request.files.getlist("photo")
        selected_album = request.form.get("album")
        new_album = request.form.get("new_album", "").strip()

        if not photos or all(p.filename == '' for p in photos):
            return "No selected photo file", 400

        folder = new_album if (selected_album == "new" and new_album) else selected_album
        if not folder:
            return "Folder name is required", 400

        folder = f"private/{folder}"

        try:
            for photo in photos:
                if photo and photo.filename != '':
                    img = fix_image_orientation(photo)
                    buffer = io.BytesIO()
                    img.save(buffer, format="JPEG", quality=90, optimize=True)
                    buffer.seek(0)
                    cloudinary.uploader.upload(buffer, folder=folder, quality="auto", fetch_format="auto")

            flash("Uploaded successfully.")
            return redirect(url_for("upload_private", last_album=folder.split('/', 1)[1]))
        except Exception as e:
            return f"Error uploading file: {str(e)}"

    # ✅ GET 请求时取出 last_album 传给模板
    last_album = request.args.get("last_album", "")
    return render_template("upload_private.html", album_names=album_names, last_album=last_album)

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
    try:
        resources = cloudinary.api.resources(type="upload", prefix="private", max_results=500)
        albums_set = set()
        for res in resources['resources']:
            parts = res['public_id'].split('/')
            if len(parts) >= 2:
                albums_set.add(parts[1])
        albums = []
        for album_name in albums_set:
            album_resources = cloudinary.api.resources(type="upload", prefix=f"private/{album_name}", max_results=1)
            cover_url = album_resources['resources'][0]['secure_url'] if album_resources['resources'] else ""
            albums.append({'name': album_name, 'cover': cover_url})
        return render_template("private_album.html", albums=albums)
    except Exception as e:
        return f"Error fetching private albums: {str(e)}"

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
    if not album or not url:
        return jsonify({"success": False, "error": "缺少 album 或 url"}), 400
    try:
        # 防止重复保存
        exists = Photo.query.filter_by(url=url).first()
        if exists:
            return jsonify({"success": True, "message": "already_exists"})
        new_photo = Photo(album=album, url=url, created_at=datetime.utcnow())
        db.session.add(new_photo)
        db.session.commit()
        return jsonify({"success": True})
    except Exception as e:
        db.session.rollback()
        return jsonify({"success": False, "error": str(e)}), 500
# --------------------------
# 启动
# --------------------------
if __name__ == "__main__":
    app.run(debug=True)

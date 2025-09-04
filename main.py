from flask import Flask, render_template, request, redirect, url_for, session, flash, jsonify
from werkzeug.utils import secure_filename
from flask_sqlalchemy import SQLAlchemy
from flask_migrate import Migrate
import cloudinary
import cloudinary.uploader
import cloudinary.api
import os
from datetime import datetime, timezone
from functools import wraps
from PIL import Image, ExifTags
import io
import time
from cloudinary.utils import api_sign_request
from sqlalchemy.pool import NullPool
from models import db, Album, AlbumCover, Photo   # ✅ 注意这里
from cloudinary.utils import cloudinary_url

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

MAIN_ALBUM_FOLDER = os.getenv("MAIN_ALBUM_FOLDER", "albums")

# --------------------------
# 数据库配置
# --------------------------
database_url = os.getenv("DATABASE_URL")
if database_url:
    app.config['SQLALCHEMY_DATABASE_URI'] = database_url
else:
    app.config['SQLALCHEMY_DATABASE_URI'] = "sqlite:///stories.db"

app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
app.config['SQLALCHEMY_ENGINE_OPTIONS'] = {
    "poolclass": NullPool
}

# ✅ 绑定 db 和 app
db.init_app(app)
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

def sync_all_to_db():
    """
    同步 Cloudinary 中的相册、封面以及所有图片到数据库。
    1. Album 表：存储相册名
    2. AlbumCover 表：存储相册封面 public_id
    3. Photo 表：存储相册每张图片 URL
    """
    main = (MAIN_ALBUM_FOLDER or "").strip('/')
    album_names = set()

    # 获取所有相册名
    if main:
        resources = cloudinary.api.resources(type="upload", prefix=f"{main}/", max_results=500)
        for res in resources.get('resources', []):
            parts = res.get('public_id', '').split('/')
            if len(parts) >= 2:
                album_names.add(parts[1])
    else:
        folders = cloudinary.api.root_folders()
        for folder in folders.get('folders', []):
            name = folder.get('name')
            if name and name != "private":
                album_names.add(name)

    created_albums = 0
    created_covers = 0
    created_photos = 0

    for name in sorted(album_names):
        # 1️⃣ Album
        album = Album.query.filter_by(name=name).first()
        if not album:
            album = Album(name=name)
            db.session.add(album)
            db.session.flush()  # 确保 album.id 可用
            created_albums += 1

        # 2️⃣ AlbumCover
        cover_entry = AlbumCover.query.filter_by(album_id=album.id).first()
        if not cover_entry:
            try:
                prefix = f"{main}/{name}" if main else name
                r = cloudinary.api.resources(type="upload", prefix=prefix, max_results=1)
                if r.get('resources'):
                    pid = r['resources'][0]['public_id']
                    cover_entry = AlbumCover(album_id=album.id, cover_public_id=pid)
                    db.session.add(cover_entry)
                    created_covers += 1
            except Exception:
                pass

        # 3️⃣ Photo
        try:
            prefix = f"{main}/{name}" if main else name
            r = cloudinary.api.resources(type="upload", prefix=prefix, max_results=500)
            for res in r.get('resources', []):
                url = res.get('secure_url')
                if url:
                    existing = Photo.query.filter_by(album_id=album.id, url=url).first()
                    if not existing:
                        p = Photo(album=album, url=url) 
                        db.session.add(p)
                        created_photos += 1
        except Exception:
            pass

    db.session.commit()
    return {
        "created_albums": created_albums,
        "created_covers": created_covers,
        "created_photos": created_photos
    }
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
# --------------------------
# Albums 列表（使用 DB 优先，未初始化 DB 时回退到 Cloudinary）
# --------------------------
@app.route("/albums")
def albums():
    try:
        albums_list = []
        # 从数据库获取所有相册
        all_albums = Album.query.order_by(Album.id).all()
        for alb in all_albums:
            # 获取对应封面
            cover = AlbumCover.query.filter_by(album_id=alb.id).first()
            if cover:
                # 生成封面 URL
                cover_url, _ = cloudinary_url(cover.cover_public_id)
            else:
                cover_url = ""  # 没有封面就留空
            albums_list.append({'name': alb.name, 'cover': cover_url})
        return render_template("album.html", albums=albums_list)
    except Exception as e:
        return f"Error fetching albums: {str(e)}"

# --------------------------
# 查看相册内容
# --------------------------
@app.route("/album/<album_name>")
def view_album(album_name):
    try:
        # 从数据库获取该相册
        album = Album.query.filter_by(name=album_name).first()
        photos = Photo.query.filter_by(album=album_name).order_by(Photo.id).all()

        images = [{"url": p.url, "id": p.id} for p in photos]
        logged_in = session.get("logged_in", False)

        return render_template(
            "view_album.html",
            album_name=album_name,
            album=album,       # ✅ 传入 album
            images=images,
            logged_in=logged_in
        )
    except Exception as e:
        return f"Error loading album: {str(e)}"


@app.route("/set_cover/<int:album_id>/<path:public_id>", methods=["POST"])
def set_cover(album_id, public_id):
    if not session.get("logged_in"):
        return jsonify(success=False, error="Unauthorized"), 403

    try:
        cover = AlbumCover.query.filter_by(album_id=album_id).first()
        if cover:
            cover.cover_public_id = public_id
        else:
            cover = AlbumCover(album_id=album_id, cover_public_id=public_id)
            db.session.add(cover)

        db.session.commit()
        return jsonify(success=True)
    except Exception as e:
        db.session.rollback()
        return jsonify(success=False, error=str(e)), 500

@app.route("/admin/sync_albums", methods=["POST", "GET"])
@login_required
def admin_sync_albums():
    try:
        res = sync_albums_to_db()
        flash(f"Synced albums: created {res['created_albums']} albums, created {res['created_covers']} covers.")
        return redirect(url_for('albums'))
    except Exception as e:
        flash(f"Sync failed: {str(e)}", "error")
        return redirect(url_for('albums'))

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
                cloudinary.uploader.upload(
                    file,
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
        MAIN_ALBUM_FOLDER=MAIN_ALBUM_FOLDER   # 👈 一定要传这个
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


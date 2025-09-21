from flask import Flask, render_template, request, redirect, url_for, session, flash, jsonify
from werkzeug.utils import secure_filename
from flask_sqlalchemy import SQLAlchemy
from flask_migrate import Migrate
import os
from datetime import datetime
from functools import wraps
from PIL import Image, ExifTags
import io, uuid, re

from sqlalchemy.pool import NullPool, QueuePool
from supabase import create_client, Client

# --------------------------
# Flask 初始化
# --------------------------
app = Flask(__name__)
app.secret_key = os.getenv('FLASK_SECRET', 'xia0720_secret')

# --------------------------
# Supabase 配置
# --------------------------
SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_KEY = os.getenv("SUPABASE_KEY")
SUPABASE_BUCKET = os.getenv("SUPABASE_BUCKET", "photos")  # 存储桶名字

supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)

MAX_IMAGE_SIZE = 3 * 1024 * 1024  # 3MB 压缩目标

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
    app.config['SQLALCHEMY_ENGINE_OPTIONS'] = {"poolclass": NullPool}

app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
db = SQLAlchemy(app)
migrate = Migrate(app, db)

# --------------------------
# 数据模型
# --------------------------
class Photo(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    album = db.Column(db.String(128), nullable=False)
    url = db.Column(db.String(512), nullable=False, unique=True)
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


# --------------------------
# 工具函数
# --------------------------
def compress_image(file_stream, max_size=MAX_IMAGE_SIZE):
    """压缩图片到 max_size 以内"""
    img = Image.open(file_stream)
    img = img.convert("RGB")

    buf = io.BytesIO()
    quality = 85
    img.save(buf, format="JPEG", quality=quality, optimize=True)
    while buf.tell() > max_size and quality > 30:
        quality -= 10
        buf.seek(0)
        buf.truncate(0)
        img.save(buf, format="JPEG", quality=quality, optimize=True)

    buf.seek(0)
    return buf


def login_required(f):
    from functools import wraps

    @wraps(f)
    def decorated(*args, **kwargs):
        if not session.get("logged_in"):
            return redirect(url_for("login", next=request.path))
        return f(*args, **kwargs)

    return decorated

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
    albums = (
        db.session.query(Photo.album, db.func.min(Photo.url))
        .filter_by(is_private=False)
        .group_by(Photo.album)
        .all()
    )
    albums_list = [{"name": a[0], "cover": a[1]} for a in albums]
    return render_template("album.html", albums=albums_list)


@app.route("/album/<album_name>")
def view_album(album_name):
    images = Photo.query.filter_by(album=album_name, is_private=False).all()
    return render_template("view_album.html", album_name=album_name, images=images, logged_in=session.get("logged_in", False))

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
        album_name = request.form.get("album") or request.form.get("new_album")
        files = request.files.getlist("photo")
        uploaded_urls = []

        for file in files:
            if not file or not file.filename:
                continue

            # Google Drive 链接直接存
            if file.filename.startswith("http"):
                url = file.filename.strip()
                new_photo = Photo(album=album_name, url=url, is_private=False)
                db.session.add(new_photo)
                db.session.commit()
                uploaded_urls.append(url)
                continue

            # 压缩
            buf = compress_image(file.stream)

            # 上传 Supabase
            safe_name = re.sub(r'[^a-zA-Z0-9_-]', '_', file.filename.rsplit(".", 1)[0]) or str(uuid.uuid4())
            path = f"{album_name}/{safe_name}.jpg"
            supabase.storage.from_(SUPABASE_BUCKET).upload(path, buf, {"upsert": True})

            public_url = supabase.storage.from_(SUPABASE_BUCKET).get_public_url(path)

            # 存数据库
            new_photo = Photo(album=album_name, url=public_url, is_private=False)
            db.session.add(new_photo)
            db.session.commit()
            uploaded_urls.append(public_url)

        return jsonify({"success": True, "urls": uploaded_urls})

    # GET: 获取已有相册名
    albums = db.session.query(Photo.album).filter_by(is_private=False).distinct().all()
    album_names = [a[0] for a in albums]
    return render_template("upload.html", album_names=album_names, last_album="")

# --------------------------
# 私密空间上传（仅登录）
# --------------------------
@app.route("/upload_private", methods=["POST"])
@login_required
def upload_private():
    album_name = request.form.get("album") or request.form.get("new_album")
    files = request.files.getlist("photo")
    uploaded_urls = []

    for file in files:
        if not file or not file.filename:
            continue

        if file.filename.startswith("http"):
            url = file.filename.strip()
            new_photo = Photo(album=album_name, url=url, is_private=True)
            db.session.add(new_photo)
            db.session.commit()
            uploaded_urls.append(url)
            continue

        buf = compress_image(file.stream)
        safe_name = re.sub(r'[^a-zA-Z0-9_-]', '_', file.filename.rsplit(".", 1)[0]) or str(uuid.uuid4())
        path = f"private/{album_name}/{safe_name}.jpg"
        supabase.storage.from_(SUPABASE_BUCKET).upload(path, buf, {"upsert": True})

        public_url = supabase.storage.from_(SUPABASE_BUCKET).get_public_url(path)

        new_photo = Photo(album=album_name, url=public_url, is_private=True)
        db.session.add(new_photo)
        db.session.commit()
        uploaded_urls.append(public_url)

    return jsonify({"success": True, "urls": uploaded_urls, "album": album_name})

# --------------------------
# 登录/登出
# --------------------------
@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        if request.form.get("username") == "xia0720" and request.form.get("password") == "qq123456":
            session["logged_in"] = True
            return redirect(url_for("story_list"))
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
    albums = (
        db.session.query(Photo.album, db.func.min(Photo.url))
        .filter_by(is_private=True)
        .group_by(Photo.album)
        .all()
    )
    album_list = [{"name": a[0], "cover": a[1]} for a in albums]
    return render_template("private_album.html", album_names=[a["name"] for a in album_list], album_covers={a["name"]: a["cover"] for a in album_list})


@app.route("/private_space/<album_name>")
@login_required
def view_private_album(album_name):
    images = Photo.query.filter_by(album=album_name, is_private=True).all()
    return render_template("view_private_album.html", album_name=album_name, images=images)


@app.route('/save_photo', methods=['POST'])
def save_photo():
    try:
        album_name = request.form.get("album")
        is_private = request.form.get("is_private") == "true"
        file_url = request.form.get("url")
        taken_at = request.form.get("taken_at")

        if not album_name or not file_url:
            return jsonify({"success": False, "error": "Missing album or file URL"}), 400

        album = Album.query.filter_by(name=album_name, is_private=is_private).first()
        if not album:
            album = Album(name=album_name, is_private=is_private)
            db.session.add(album)
            db.session.commit()

        photo = Photo(album=album.name, url=file_url, is_private=is_private, taken_at=taken_at)
        db.session.add(photo)
        db.session.commit()

        return jsonify({"success": True, "url": file_url})
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500

# --------------------------
# 启动
# --------------------------
if __name__ == "__main__":
    app.run(debug=True)

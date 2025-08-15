from flask import Flask, render_template, request, redirect, url_for, session, flash, abort
from werkzeug.utils import secure_filename
from flask_sqlalchemy import SQLAlchemy
from flask_migrate import Migrate
import cloudinary
import cloudinary.uploader
import cloudinary.api
import os
from datetime import datetime
from functools import wraps
from werkzeug.utils import secure_filename

app = Flask(__name__)
app.secret_key = os.getenv("FLASK_SECRET", "xia0720_secret")
# 限制上传文件最大 200MB
app.config['MAX_CONTENT_LENGTH'] = 200 * 1024 * 1024

# 配置 Cloudinary
cloudinary.config(
    cloud_name='dpr0pl2tf',
    api_key='548549517251566',
    api_secret='9o-PlPBRQzQPfuVCQfaGrUV3_IE'
)

# 支持的类型
IMAGE_EXTENSIONS = {'png', 'jpg', 'jpeg', 'gif', 'bmp', 'webp'}
VIDEO_EXTENSIONS = {'mp4', 'avi', 'mov', 'mkv', 'webm'}

def allowed_file(filename):
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in IMAGE_EXTENSIONS.union(VIDEO_EXTENSIONS)

# 数据库配置（优先 Railway）
database_url = os.getenv("DATABASE_URL")
if database_url:
    app.config['SQLALCHEMY_DATABASE_URI'] = database_url
else:
    app.config['SQLALCHEMY_DATABASE_URI'] = "sqlite:///stories.db"

app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
db = SQLAlchemy(app)
migrate = Migrate(app, db)

# ---------- 模板全局变量：所有模板都能拿到 logged_in ----------
@app.context_processor
def inject_logged_in():
    return dict(logged_in=session.get("logged_in", False))

# ---------- 简单的登录保护装饰器 ----------
def login_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if not session.get("logged_in"):
            # 不在导航栏暴露 login，但你可手动访问 /login
            return redirect(url_for("login", next=request.path))
        return f(*args, **kwargs)
    return decorated

# ---------- 数据模型 ----------
class Story(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    text = db.Column(db.Text, nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    images = db.relationship("Image", backref="story", cascade="all, delete-orphan")

class Image(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    image_url = db.Column(db.String(255), nullable=False)
    story_id = db.Column(db.Integer, db.ForeignKey("story.id"), nullable=False)

# ---------- 路由 ----------
@app.route("/")
def index():
    return render_template("index.html")

@app.route("/gallery")
def gallery():
    return render_template("gallery.html")
    
@app.route("/about")
def about():
    return render_template("about.html")

# 原来不设置相册密码：Album 列表（Cloudinary folders）
@app.route("/album")
def albums():
    try:
        folders = cloudinary.api.root_folders()
        albums = []
        for folder in folders.get('folders', []):
            folder_name = folder['name']
            # 忽略私密区与视频根目录
            if folder_name in ("private", "videos"):
                continue
            # 仅用图片做封面
            resources = cloudinary.api.resources(
                type="upload",
                prefix=folder_name,
                resource_type="image",
                max_results=1
            )
            cover_url = resources['resources'][0]['secure_url'] if resources['resources'] else ""
            albums.append({'name': folder_name, 'cover': cover_url})
        return render_template("album.html", albums=albums)
    except Exception as e:
        return f"Error fetching albums: {str(e)}"

@app.route("/album/<album_name>", methods=["GET", "POST"])
def view_album(album_name):
    try:
        resources = cloudinary.api.resources(
            type="upload",
            prefix=album_name,
            resource_type="image",   # 只取图片
            max_results=500
        )
        images = [{"public_id": img["public_id"], "secure_url": img["secure_url"]}
                  for img in resources.get("resources", [])]
        logged_in = session.get("logged_in", False)
        return render_template("view_album.html", album_name=album_name, images=images, logged_in=logged_in)
    except Exception as e:
        return f"Error loading album: {str(e)}"
        
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


@app.route("/story")
def story():
    stories = Story.query.order_by(Story.created_at.desc()).all()
    return render_template("story_list.html", stories=stories)

@app.route("/story/<int:story_id>")
def story_detail(story_id):
    story = Story.query.get_or_404(story_id)
    return render_template("story_detail.html", story=story)

# 仅登录后可发布新 Story
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
                    db.session.add(Image(image_url=img_url, story=new_story))

        db.session.commit()
        flash("Story uploaded successfully!", "success")
        return redirect(url_for("story"))

    return render_template("upload_story.html")

# 仅登录后可编辑
@app.route("/story/<int:story_id>/edit", methods=["GET", "POST"])
@login_required
def edit_story(story_id):
    story = Story.query.get_or_404(story_id)
    if request.method == "POST":
        text = request.form.get("text")
        if not text or text.strip() == "":
            flash("故事内容不能为空", "error")
            return render_template("edit_story.html", story=story)

        story.text = text.strip()

        files = request.files.getlist("story_images")
        for file in files:
            if file and file.filename:
                upload_result = cloudinary.uploader.upload(file)
                img_url = upload_result.get("secure_url")
                if img_url:
                    db.session.add(Image(image_url=img_url, story=story))

        db.session.commit()
        flash("故事已更新", "success")
        return redirect(url_for("story_detail", story_id=story.id))

    return render_template("edit_story.html", story=story)

# 仅登录后可删除
@app.route("/delete_story/<int:story_id>", methods=["POST"])
@login_required
def delete_story(story_id):
    story = Story.query.get_or_404(story_id)
    db.session.delete(story)
    db.session.commit()
    flash("Story deleted.", "info")
    return redirect(url_for("story"))

# Cloudinary folder 上传：仅登录后可见/可用
@app.route("/upload", methods=["GET", "POST"])
@login_required
def upload():
    # 取已有相册列表，用于下拉框（排除 private 与 videos）
    try:
        result = cloudinary.api.root_folders()
        album_names = [folder['name'] for folder in result.get('folders', [])
                       if folder['name'] not in ("private", "videos")]
    except Exception as e:
        album_names = []
        print("Error fetching folders:", e)

    if request.method == "POST":
        files = request.files.getlist("media_files")
        selected_album = request.form.get("album")
        new_album = request.form.get("new_album", "").strip()

        if not files or all(f.filename == '' for f in files):
            flash("请选择至少一个文件。", "warning")
            return redirect(request.url)

        # 决定图片存放到哪个相册
        if selected_album == "new":
            if not new_album:
                flash("请输入新相册名。", "error")
                return redirect(request.url)
            album_folder = new_album
        else:
            album_folder = selected_album

        if not album_folder:
            flash("相册名不能为空。", "error")
            return redirect(request.url)

        img_count, vid_count = 0, 0
        errors = []

        for f in files:
            if not f or f.filename == "":
                continue
            try:
                # 根据 mimetype 分流
                if f.mimetype.startswith("image/"):
                    # 图片 -> 选中的相册文件夹
                    cloudinary.uploader.upload(
                        f,
                        folder=album_folder,
                        resource_type="image"
                    )
                    img_count += 1
                elif f.mimetype.startswith("video/"):
                    # 视频 -> 统一放到 videos 根目录
                    cloudinary.uploader.upload(
                        f,
                        folder="videos",
                        resource_type="video"
                    )
                    vid_count += 1
                else:
                    errors.append(f"不支持的文件类型：{f.filename}")
            except Exception as e:
                errors.append(f"{f.filename}: {str(e)}")

        if img_count or vid_count:
            flash(f"上传成功：{img_count} 张图片，{vid_count} 个视频。", "success")
        if errors:
            flash("部分文件上传失败： " + "；".join(errors), "error")

        # 根据本次上传内容决定跳转
        if img_count > 0 and vid_count == 0:
            return redirect(url_for("view_album", album_name=album_folder))
        elif vid_count > 0 and img_count == 0:
            return redirect(url_for("videos"))
        else:
            # 混合上传或全部失败，就回上传页
            return redirect(url_for("upload"))

    return render_template("upload.html", album_names=album_names)

@app.route("/videos")
def videos():
    try:
        # 获取 videos 文件夹下的视频
        resources = cloudinary.api.resources(type="upload", prefix="videos", max_results=500, resource_type="video")
        videos = [{"url": r["secure_url"], "public_id": r["public_id"]} for r in resources["resources"]]
        return render_template("videos.html", videos=videos)
    except Exception as e:
        return f"Error fetching videos: {str(e)}"

# Login / Logout（路由存在，但不在导航栏展示）
@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        username = request.form.get("username", "")
        password = request.form.get("password", "")
        if username == "xia0720" and password == "qq123456":
            session["logged_in"] = True
            flash("Logged in.")
            next_url = request.args.get("next")
            return redirect(next_url or url_for("story"))
        else:
            flash("Invalid credentials.")
            return redirect(url_for("login"))
    return render_template("login.html")

@app.route("/logout")
def logout():
    session.pop("logged_in", None)
    flash("Logged out.")
    return redirect(url_for("index"))

# Test DB connectivity
@app.route("/test-db")
def test_db():
    try:
        db.session.execute("SELECT 1")
        return "DB OK"
    except Exception as e:
        return f"DB failed: {str(e)}", 500

# ---------- Private-space ----------
@app.route("/private_space")
@login_required
def private_space():
    try:
        # 获取 private/ 下的所有资源
        resources = cloudinary.api.resources(type="upload", prefix="private", max_results=500)
        # 提取不同子文件夹名作为相册
        albums_set = set()
        for res in resources['resources']:
            # 公共 id 的前缀是 private/album_name/filename
            public_id = res['public_id']
            parts = public_id.split('/')
            if len(parts) >= 2:
                albums_set.add(parts[1])
        albums = []
        for album_name in albums_set:
            # 取封面图
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

@app.route("/upload_private", methods=["GET", "POST"])
@login_required
def upload_private():
    try:
        # 获取 private/ 下已有相册
        resources = cloudinary.api.resources(type="upload", prefix="private", max_results=500)
        album_names_set = set()
        for res in resources['resources']:
            parts = res['public_id'].split('/')
            if len(parts) >= 2:
                album_names_set.add(parts[1])
        album_names = list(album_names_set)
    except Exception as e:
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

        folder = f"private/{folder}"  # 上传到 private/ 下
        try:
            for photo in photos:
                if photo and photo.filename != '':
                    cloudinary.uploader.upload(photo, folder=folder)
            flash("Uploaded successfully.")
            return redirect(url_for("upload_private"))
        except Exception as e:
            return f"Error uploading file: {str(e)}"

    return render_template("upload_private.html", album_names=album_names)


if __name__ == "__main__":
    app.run(debug=True)

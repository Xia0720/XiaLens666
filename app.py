from flask import Flask, render_template, request, redirect, url_for, session, flash
from werkzeug.security import generate_password_hash, check_password_hash
from flask_sqlalchemy import SQLAlchemy
from flask_migrate import Migrate
import cloudinary
import cloudinary.uploader
import cloudinary.api
import os
from datetime import datetime
from functools import wraps
from app.models import Album, Story, Image

app = Flask(__name__)
app.secret_key = os.getenv("FLASK_SECRET", "xia0720_secret")

# Cloudinary 配置
cloudinary.config(
    cloud_name='dpr0pl2tf',
    api_key='548549517251566',
    api_secret='9o-PlPBRQzQPfuVCQfaGrUV3_IE'
)

# 数据库配置
database_url = os.getenv("DATABASE_URL")
if database_url:
    app.config['SQLALCHEMY_DATABASE_URI'] = database_url
else:
    app.config['SQLALCHEMY_DATABASE_URI'] = "sqlite:///stories.db"

app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
db = SQLAlchemy(app)
migrate = Migrate(app, db)

# ----------------- 数据模型 -----------------
class Album(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(255), unique=True, nullable=False)
    password_hash = db.Column(db.String(255), nullable=True)

    def set_password(self, password):
        self.password_hash = generate_password_hash(password)

    def check_password(self, password):
        if not self.password_hash:
            return True
        return check_password_hash(self.password_hash, password)

class Story(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    text = db.Column(db.Text, nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    images = db.relationship("Image", backref="story", cascade="all, delete-orphan")

class Image(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    image_url = db.Column(db.String(255), nullable=False)
    story_id = db.Column(db.Integer, db.ForeignKey("story.id"), nullable=False)

# ----------------- 登录保护装饰器 -----------------
def login_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if not session.get("logged_in"):
            return redirect(url_for("login", next=request.path))
        return f(*args, **kwargs)
    return decorated

# ----------------- 模板全局变量 -----------------
@app.context_processor
def inject_logged_in():
    return dict(logged_in=session.get("logged_in", False))

# ----------------- 路由 -----------------
@app.route("/")
def index():
    albums = Album.query.all()
    return render_template("index.html", albums=albums)

@app.route("/gallery")
def gallery():
    return render_template("gallery.html")

@app.route("/about")
def about():
    return render_template("about.html")

@app.route("/album")
def albums():
    try:
        folders = cloudinary.api.root_folders()
        albums_list = []
        for folder in folders.get('folders', []):
            subfolder_name = folder['name']
            resources = cloudinary.api.resources(type="upload", prefix=subfolder_name, max_results=1)
            cover_url = resources['resources'][0]['secure_url'] if resources['resources'] else ""
            albums_list.append({'name': subfolder_name, 'cover': cover_url})
        return render_template("album.html", albums=albums_list)
    except Exception as e:
        return f"Error fetching albums: {str(e)}"

# 查看单个相册
@app.route("/album/<album_name>", methods=["GET", "POST"])
def view_album(album_name):
    album = Album.query.filter_by(name=album_name).first()
    if album and album.password_hash:
        if request.method == "POST":
            password = request.form.get("password")
            if album.check_password(password):
                session[f"album_access_{album_name}"] = True
                return redirect(url_for("view_album", album_name=album_name))
            else:
                flash("Incorrect password.")
        if not session.get(f"album_access_{album_name}"):
            return render_template("album_password.html", album_name=album_name)
    # 无密码或已授权
    try:
        resources = cloudinary.api.resources(type="upload", prefix=album_name)
        images = resources["resources"]
        return render_template("view_album.html", album_name=album_name, images=images)
    except Exception as e:
        return f"Error loading album: {str(e)}"

# 设置/修改相册密码
@app.route("/album/<album_name>/set_password", methods=["GET", "POST"])
@login_required
def set_album_password(album_name):
    album = Album.query.filter_by(name=album_name).first()
    if not album:
        album = Album(name=album_name)
        db.session.add(album)
    if request.method == "POST":
        password = request.form.get("password")
        if password:
            album.set_password(password)
        else:
            album.password_hash = None
        db.session.commit()
        flash("Password updated.")
        return redirect(url_for("albums"))
    return render_template("set_album_password.html", album=album)

# 删除图片（管理员）
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


# 故事列表页
@app.route("/story")
def story():
    stories = Story.query.order_by(Story.created_at.desc()).all()
    return render_template("story_list.html", stories=stories)

# 故事详情页
@app.route("/story/<int:story_id>")
def story_detail(story_id):
    story = Story.query.get_or_404(story_id)
    return render_template("story_detail.html", story=story)

# 上传故事，登录保护
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

# 编辑故事，登录保护
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

# 删除故事，登录保护
@app.route("/delete_story/<int:story_id>", methods=["POST"])
@login_required
def delete_story(story_id):
    story = Story.query.get_or_404(story_id)
    db.session.delete(story)
    db.session.commit()
    flash("Story deleted.", "info")
    return redirect(url_for("story"))

# 多图上传到指定相册，登录保护
@app.route("/upload", methods=["GET", "POST"])
@login_required
def upload():
    if request.method == "POST":
        photos = request.files.getlist("photo")
        folder = request.form.get("folder")
        if not photos or all(p.filename == '' for p in photos):
            return "No selected photo file", 400
        if not folder:
            return "Folder name is required", 400
        try:
            for photo in photos:
                if photo and photo.filename != '':
                    cloudinary.uploader.upload(photo, folder=folder)
            flash("Uploaded successfully.")
            return redirect(url_for("upload"))
        except Exception as e:
            return f"Error uploading file: {str(e)}"
    return render_template("upload.html")

# 登录/登出
@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        username = request.form.get("username")
        password = request.form.get("password")
        if username == "xia0720" and password == "qq123456":
            session["logged_in"] = True
            flash("Logged in.")
            next_url = request.args.get("next")
            return redirect(next_url or url_for("index"))
        else:
            flash("Invalid credentials.")
            return redirect(url_for("login"))
    return render_template("login.html")

@app.route("/logout")
def logout():
    session.pop("logged_in", None)
    flash("Logged out.")
    return redirect(url_for("index"))


if __name__ == "__main__":
    app.run(debug=True)



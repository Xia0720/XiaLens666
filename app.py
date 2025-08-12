from flask import Flask, render_template, request, redirect, url_for, session, flash, abort
from werkzeug.utils import secure_filename
from flask_sqlalchemy import SQLAlchemy
from flask_migrate import Migrate
import cloudinary
import cloudinary.uploader
import cloudinary.api
import os
from datetime import datetime

app = Flask(__name__)
app.secret_key = os.getenv("FLASK_SECRET", "xia0720_secret")

# 配置 Cloudinary (建议用环境变量)
cloudinary.config(
    cloud_name='dpr0pl2tf',
    api_key='548549517251566',
    api_secret='9o-PlPBRQzQPfuVCQfaGrUV3_IE'
)

# Database config: 优先使用环境变量 DATABASE_URL (Railway)
database_url = os.getenv("DATABASE_URL")
if database_url:
    app.config['SQLALCHEMY_DATABASE_URI'] = database_url
else:
    # fallback 本地 sqlite，方便本地开发
    app.config['SQLALCHEMY_DATABASE_URI'] = "sqlite:///stories.db"

app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
db = SQLAlchemy(app)
migrate = Migrate(app, db)

# admin secret (可选，用 ?admin_key=xxx 自动登录)
ADMIN_SECRET = os.getenv("ADMIN_SECRET", "superxia0720")

ALLOWED_EXTENSIONS = {"png", "jpg", "jpeg", "gif"}


def allowed_file(filename):
    return "." in filename and filename.rsplit(".", 1)[1].lower() in ALLOWED_EXTENSIONS


# Model
class Story(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    text = db.Column(db.Text, nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    images = db.relationship("StoryImage", backref="story", cascade="all, delete-orphan")

class StoryImage(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    story_id = db.Column(db.Integer, db.ForeignKey("story.id"), nullable=False)
    image_url = db.Column(db.String(1000), nullable=False)


# 在模板内全局可用 logged_in、request（request 自带），避免每次都传
@app.context_processor
def inject_logged_in():
    return dict(logged_in=session.get("logged_in", False))


# 自动通过 URL 参数 admin_key 登录（可选）
@app.before_request
def auto_login_with_secret():
    # 只做自动登录，不做强制 redirect
    if session.get("logged_in"):
        return
    admin_key = request.args.get("admin_key")
    if admin_key and admin_key == ADMIN_SECRET:
        session["logged_in"] = True


# 首页
@app.route("/")
def index():
    return render_template("index.html")


@app.route("/gallery")
def gallery():
    return render_template("gallery.html")


@app.route("/about")
def about():
    return render_template("about.html")


# Album 列表（Cloudinary folders）
@app.route("/album")
def albums():
    try:
        folders = cloudinary.api.root_folders()
        albums = []
        for folder in folders.get('folders', []):
            subfolder_name = folder['name']
            resources = cloudinary.api.resources(type="upload", prefix=subfolder_name, max_results=1)
            cover_url = resources['resources'][0]['secure_url'] if resources['resources'] else ""
            albums.append({'name': subfolder_name, 'cover': cover_url})
        return render_template("album.html", albums=albums)
    except Exception as e:
        return f"Error fetching albums: {str(e)}"


@app.route("/album/<album_name>")
def view_album(album_name):
    try:
        resources = cloudinary.api.resources(type="upload", prefix=album_name)
        image_urls = [img["secure_url"] for img in resources["resources"]]
        return render_template("view_album.html", album_name=album_name, image_urls=image_urls)
    except Exception as e:
        return f"Error loading album: {str(e)}"


# Story 列表（显示）
@app.route("/story")
def story():
    stories = Story.query.order_by(Story.created_at.desc()).all()
    return render_template("story.html", stories=stories)


# Upload story (create) — 支持多图上传
@app.route("/upload_story", methods=["GET", "POST"])
def upload_story():
    if not session.get("logged_in"):
        flash("Please login to upload stories.")
        return redirect(url_for("login"))

    if request.method == "POST":
        text = request.form.get("story_text", "").strip()
        files = request.files.getlist("story_images")  # 获取多文件

        if not text:
            flash("Please enter story text.")
            return redirect(url_for("upload_story"))

        new_story = Story(text=text)
        db.session.add(new_story)
        db.session.flush()  # 先flush以便获取 new_story.id

        # 上传多张图片
        for file in files:
            if file and allowed_file(file.filename):
                try:
                    upload_result = cloudinary.uploader.upload(file)
                    image_url = upload_result.get("secure_url")
                    story_image = StoryImage(story_id=new_story.id, image_url=image_url)
                    db.session.add(story_image)
                except Exception as e:
                    flash(f"Image upload failed: {str(e)}")
                    db.session.rollback()
                    return redirect(url_for("upload_story"))

        db.session.commit()
        flash("Story uploaded.")
        return redirect(url_for("story"))

    return render_template("upload_story.html")


# Edit story
@app.route("/edit_story/<int:story_id>", methods=["GET", "POST"])
def edit_story(story_id):
    if not session.get("logged_in"):
        flash("Please login to edit.")
        return redirect(url_for("login"))

    story_obj = Story.query.get_or_404(story_id)

    if request.method == "POST":
        text = request.form.get("story_text", "").strip()
        files = request.files.getlist("story_images")  # 支持多图编辑时上传

        if text:
            story_obj.text = text

        # 如果上传了新图片，添加进去（不删除旧图）
        for file in files:
            if file and allowed_file(file.filename):
                try:
                    upload_result = cloudinary.uploader.upload(file)
                    image_url = upload_result.get("secure_url")
                    story_image = StoryImage(story_id=story_obj.id, image_url=image_url)
                    db.session.add(story_image)
                except Exception as e:
                    flash(f"Image upload failed: {str(e)}")
                    return redirect(url_for("edit_story", story_id=story_id))

        db.session.commit()
        flash("Story updated.")
        return redirect(url_for("story"))

    return render_template("edit_story.html", story=story_obj)


# Delete story (POST to avoid accidental deletes)
@app.route("/delete_story/<int:story_id>", methods=["POST"])
def delete_story(story_id):
    if not session.get("logged_in"):
        flash("Please login to delete.")
        return redirect(url_for("login"))

    story_obj = Story.query.get_or_404(story_id)
    # NOTE: not removing image from Cloudinary to keep it simple
    db.session.delete(story_obj)
    db.session.commit()
    flash("Story deleted.")
    return redirect(url_for("story"))


# Generic upload page (Cloudinary folder upload)
@app.route("/upload", methods=["GET", "POST"])
def upload():
    if not session.get("logged_in"):
        # show login only on certain pages — but here redirect to login
        return redirect(url_for("login"))

    if request.method == "POST":
        photo = request.files.get("photo")
        folder = request.form.get("folder")
        if not photo or photo.filename == '':
            return "No selected photo file", 400
        if not folder:
            return "Folder name is required", 400
        try:
            cloudinary.uploader.upload(photo, folder=folder)
            flash("Uploaded successfully.")
            return redirect(url_for("upload"))
        except Exception as e:
            return f"Error uploading file: {str(e)}"
    return render_template("upload.html")


# Login / Logout
@app.route("/login", methods=["GET", "POST"])
def login():
    # keep simple auth
    if request.method == "POST":
        username = request.form.get("username", "")
        password = request.form.get("password", "")
        if username == "xia0720" and password == "qq123456":
            session["logged_in"] = True
            flash("Logged in.")
            return redirect(url_for("story"))
        else:
            flash("Invalid credentials.")
            return redirect(url_for("login"))
    return render_template("login.html")


@app.route("/logout")
def logout():
    session.pop("logged_in", None)
    flash("Logged out.")
    # go home after logout
    return redirect(url_for("index"))


# Test DB connectivity
@app.route("/test-db")
def test_db():
    try:
        db.session.execute("SELECT 1")
        return "DB OK"
    except Exception as e:
        return f"DB failed: {str(e)}", 500


if __name__ == "__main__":
    with app.app_context():
        db.create_all()
    app.run(debug=True, host="0.0.0.0", port=int(os.getenv("PORT", 5000)))


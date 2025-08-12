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

# 配置 Cloudinary
cloudinary.config(
    cloud_name='dpr0pl2tf',
    api_key='548549517251566',
    api_secret='9o-PlPBRQzQPfuVCQfaGrUV3_IE'
)

# 数据库配置（优先 Railway）
database_url = os.getenv("DATABASE_URL")
if database_url:
    app.config['SQLALCHEMY_DATABASE_URI'] = database_url
else:
    app.config['SQLALCHEMY_DATABASE_URI'] = "sqlite:///stories.db"

app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
db = SQLAlchemy(app)
migrate = Migrate(app, db)

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

@app.route("/story")
def story():
    stories = Story.query.order_by(Story.created_at.desc()).all()
    logged_in = session.get("logged_in", False)  # 这里加了
    return render_template("story_list.html", stories=stories, logged_in=logged_in)

@app.route("/story/<int:story_id>")
def story_detail(story_id):
    story = Story.query.get_or_404(story_id)
    logged_in = session.get("logged_in", False)
    return render_template("story_detail.html", story=story, logged_in=logged_in)

@app.route("/upload_story", methods=["GET", "POST"])
def upload_story():
    if request.method == "POST":
        story_text = request.form.get("story_text")
        files = request.files.getlist("story_images")

        if not story_text:
            flash("Story content is required.", "error")
            return redirect(request.url)

        new_story = Story(text=story_text)
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

@app.route("/story/<int:story_id>/edit", methods=["GET", "POST"])
def edit_story(story_id):
    story = Story.query.get_or_404(story_id)
    if request.method == "POST":
        text = request.form.get("text")
        if not text or text.strip() == "":
            flash("故事内容不能为空", "error")
            return render_template("edit_story.html", story=story)

        story.text = text

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

@app.route("/delete_story/<int:story_id>", methods=["POST"])
def delete_story(story_id):
    story = Story.query.get_or_404(story_id)
    db.session.delete(story)
    db.session.commit()
    flash("Story deleted.", "info")
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
    app.run(debug=True)

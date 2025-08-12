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
    return redirect(url_for("story"))

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

@app.route("/edit_story/<int:story_id>", methods=["GET", "POST"])
def edit_story(story_id):
    story = Story.query.get_or_404(story_id)
    if request.method == "POST":
        story.text = request.form.get("story_text")
        files = request.files.getlist("story_images")

        for file in files:
            if file and file.filename:
                upload_result = cloudinary.uploader.upload(file)
                img_url = upload_result.get("secure_url")
                if img_url:
                    db.session.add(Image(image_url=img_url, story=story))

        db.session.commit()
        flash("Story updated successfully!", "success")
        return redirect(url_for("story"))

    return render_template("edit_story.html", story=story)

@app.route("/delete_story/<int:story_id>", methods=["POST"])
def delete_story(story_id):
    story = Story.query.get_or_404(story_id)
    db.session.delete(story)
    db.session.commit()
    flash("Story deleted.", "info")
    return redirect(url_for("story"))

if __name__ == "__main__":
    app.run(debug=True)

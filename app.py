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

# 数据库配置，优先用环境变量 DATABASE_URL，没设置就用本地 sqlite
database_url = os.getenv("DATABASE_URL")
if database_url:
    app.config['SQLALCHEMY_DATABASE_URI'] = database_url
else:
    app.config['SQLALCHEMY_DATABASE_URI'] = "sqlite:///stories.db"

app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False

db = SQLAlchemy(app)
migrate = Migrate(app, db)

ADMIN_SECRET = os.getenv("ADMIN_SECRET", "superxia0720")

ALLOWED_EXTENSIONS = {"png", "jpg", "jpeg", "gif"}


def allowed_file(filename):
    return "." in filename and filename.rsplit(".", 1)[1].lower() in ALLOWED_EXTENSIONS


# Models
class Story(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    title = db.Column(db.String(100))
    content = db.Column(db.Text)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    images = db.relationship('StoryImage', backref='story', lazy=True)


class StoryImage(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    story_id = db.Column(db.Integer, db.ForeignKey('story.id'), nullable=False)
    image_url = db.Column(db.String(300))


@app.context_processor
def inject_logged_in():
    return dict(logged_in=session.get("logged_in", False))


@app.before_request
def auto_login_with_secret():
    if session.get("logged_in"):
        return
    admin_key = request.args.get("admin_key")
    if admin_key and admin_key == ADMIN_SECRET:
        session["logged_in"] = True


@app.route("/")
def index():
    return render_template("index.html")


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
    return render_template("story.html", stories=stories)


@app.route('/story/new', methods=['GET', 'POST'])
def new_story():
    if request.method == 'POST':
        title = request.form.get('title')
        content = request.form.get('content')
        files = request.files.getlist('images')

        if not title or not content:
            return "标题和内容不能为空", 400

        story = Story(title=title, content=content)
        db.session.add(story)
        db.session.flush()  # 先flush获取id

        for file in files:
            if file and allowed_file(file.filename):
                try:
                    upload_result = cloudinary.uploader.upload(file)
                    image_url = upload_result.get('secure_url')
                    img = StoryImage(story_id=story.id, image_url=image_url)
                    db.session.add(img)
                except Exception as e:
                    db.session.rollback()
                    return f"图片上传失败: {str(e)}", 500

        db.session.commit()
        return redirect(url_for('show_story', story_id=story.id))

    return render_template('edit_story.html')


@app.route('/story/<int:story_id>')
def show_story(story_id):
    story = Story.query.get_or_404(story_id)
    return render_template('story.html', story=story)


@app.route("/login", methods=["GET", "POST"])
def login():
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
    return redirect(url_for("index"))


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

from flask import Flask, render_template, request, redirect, url_for, session, flash
from werkzeug.utils import secure_filename
import cloudinary
import cloudinary.uploader
import cloudinary.api
from flask_sqlalchemy import SQLAlchemy
import os

app = Flask(__name__)
app.secret_key = "xia0720"  # 用于 session 加密

# 配置 Cloudinary（建议用环境变量存）
cloudinary.config(
    cloud_name='dpr0pl2tf',
    api_key='548549517251566',
    api_secret='9o-PlPBRQzQPfuVCQfaGrUV3_IE'
)

# 数据库配置（Railway 自动提供 DATABASE_URL）
app.config['SQLALCHEMY_DATABASE_URL'] = os.getenv("DATABASE_URL")
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
db = SQLAlchemy(app)

# 故事数据表
class Story(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    text = db.Column(db.Text, nullable=False)
    image_url = db.Column(db.String(500), nullable=True)

# 上传允许的图片格式
ALLOWED_EXTENSIONS = {"png", "jpg", "jpeg", "gif"}

def allowed_file(filename):
    return "." in filename and filename.rsplit(".", 1)[1].lower() in ALLOWED_EXTENSIONS

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

# 相册列表
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
        return render_template("album.html", albums=albums, logged_in=session.get("logged_in", False))
    except Exception as e:
        return f"Error fetching albums: {str(e)}"

# 查看单个相册内容
@app.route("/album/<album_name>")
def view_album(album_name):
    try:
        resources = cloudinary.api.resources(type="upload", prefix=album_name)
        image_urls = [img["secure_url"] for img in resources["resources"]]
        return render_template("view_album.html", album_name=album_name, image_urls=image_urls, logged_in=session.get("logged_in", False))
    except Exception as e:
        return f"Error loading album: {str(e)}"

# 查看故事
@app.route("/story")
def story():
    stories = Story.query.order_by(Story.id.desc()).all()
    story_list = [{"text": s.text, "image": s.image_url} for s in stories]
    return render_template("story.html", stories=story_list, logged_in=session.get("logged_in", False))

# 上传故事（需登录）
@app.route("/upload_story", methods=["GET", "POST"])
def upload_story():
    if not session.get("logged_in"):
        flash("请先登录才能上传故事")
        return redirect(url_for("login"))

    if request.method == "POST":
        text = request.form.get("story_text", "")
        file = request.files.get("story_image")

        image_url = None
        if file and allowed_file(file.filename):
            upload_result = cloudinary.uploader.upload(file)
            image_url = upload_result.get("secure_url")

        new_story = Story(text=text, image_url=image_url)
        db.session.add(new_story)
        db.session.commit()

        flash("故事上传成功！")
        return redirect(url_for("story"))

    return render_template("upload_story.html", logged_in=True)

# 上传图片到 Cloudinary（需登录）
@app.route("/upload", methods=["GET", "POST"])
def upload():
    if not session.get("logged_in"):
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
            flash("上传成功")
            return redirect(url_for("upload"))
        except Exception as e:
            return f"Error uploading file: {str(e)}"

    return render_template("upload.html", logged_in=True)

# 登录
@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        username = request.form["username"]
        password = request.form["password"]

        if username == "xia0720" and password == "qq123456":
            session["logged_in"] = True
            flash("登录成功")
            return redirect(url_for("story"))
        else:
            flash("用户名或密码错误")
            return redirect(url_for("login"))

    return render_template("login.html", logged_in=session.get("logged_in", False))

# 登出
@app.route("/logout")
def logout():
    session.clear()
    flash("已退出登录")
    return redirect(url_for("index"))

if __name__ == "__main__":
    # 第一次运行时初始化数据库
    with app.app_context():
        db.create_all()
    app.run(debug=True)

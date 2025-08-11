from flask import Flask, render_template, request, redirect, url_for, session, flash
from werkzeug.utils import secure_filename
import cloudinary
import cloudinary.uploader
import cloudinary.api
from flask_sqlalchemy import SQLAlchemy
import os

app = Flask(__name__)
app.secret_key = "xia0720"  # session加密密钥

# 配置 Cloudinary (建议用环境变量)
cloudinary.config(
    cloud_name='dpr0pl2tf',
    api_key='548549517251566',
    api_secret='9o-PlPBRQzQPfuVCQfaGrUV3_IE'
)

# 数据库配置，Railway默认提供DATABASE_URL环境变量
app.config['SQLALCHEMY_DATABASE_URI'] = os.getenv(
    "DATABASE_URL",
    "postgresql://postgres:VGsQnBUSMnPCJCwQJJcRmbuStxvRWKrQ@trolley.proxy.rlwy.net:59000/railway"
)
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
db = SQLAlchemy(app)

# Story数据模型
class Story(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    text = db.Column(db.Text, nullable=False)
    image_url = db.Column(db.String(500), nullable=True)

ALLOWED_EXTENSIONS = {"png", "jpg", "jpeg", "gif"}

def allowed_file(filename):
    return "." in filename and filename.rsplit(".", 1)[1].lower() in ALLOWED_EXTENSIONS

# 首页
@app.route("/")
def index():
    return render_template("index.html", logged_in=session.get("logged_in", False))

@app.route("/about")
def about():
    return render_template("about.html", logged_in=session.get("logged_in", False))

@app.route("/album")
def albums():
    try:
        folders = cloudinary.api.root_folders()
        albums = []
        for folder in folders.get('folders', []):
            name = folder['name']
            resources = cloudinary.api.resources(type="upload", prefix=name, max_results=1)
            cover_url = resources['resources'][0]['secure_url'] if resources['resources'] else ""
            albums.append({'name': name, 'cover': cover_url})
        return render_template("album.html", albums=albums, logged_in=session.get("logged_in", False))
    except Exception as e:
        flash(f"Error fetching albums: {str(e)}")
        return render_template("album.html", albums=[], logged_in=session.get("logged_in", False))

@app.route("/album/<album_name>")
def view_album(album_name):
    try:
        resources = cloudinary.api.resources(type="upload", prefix=album_name)
        image_urls = [img["secure_url"] for img in resources["resources"]]
        return render_template("view_album.html", folder=album_name, image_urls=image_urls, logged_in=session.get("logged_in", False))
    except Exception as e:
        flash(f"Error loading album: {str(e)}")
        return redirect(url_for("albums"))

@app.route("/story")
def story():
    # 查询数据库，获取所有故事模型对象，按 id 降序排列
    stories = Story.query.order_by(Story.id.desc()).all()
    return render_template("story.html", stories=stories, logged_in=session.get("logged_in", False))

@app.route("/upload_story", methods=["GET", "POST"])
def upload_story():
    if not session.get("logged_in"):
        flash("请先登录才能上传故事")
        return redirect(url_for("login"))

    if request.method == "POST":
        text = request.form.get("story_text", "").strip()
        file = request.files.get("story_image")

        image_url = None
        if file and allowed_file(file.filename):
            upload_result = cloudinary.uploader.upload(file)
            image_url = upload_result.get("secure_url")

        if not text:
            flash("故事文本不能为空")
            return redirect(url_for("upload_story"))

        new_story = Story(text=text, image_url=image_url)
        db.session.add(new_story)
        db.session.commit()

        flash("故事上传成功！")
        return redirect(url_for("story"))

    return render_template("upload_story.html", logged_in=True)
    
# 编辑故事页
@app.route("/edit_story/<int:story_id>", methods=["GET", "POST"])
def edit_story(story_id):
    if not session.get("logged_in"):
        return redirect(url_for("login"))

    story = Story.query.get_or_404(story_id)

    if request.method == "POST":
        story.text = request.form["story_text"]

        # 如果用户上传了新图片，替换
        if "story_image" in request.files and request.files["story_image"].filename != "":
            image_file = request.files["story_image"]
            filename = secure_filename(image_file.filename)

            # 删除旧图片
            old_image_path = os.path.join(app.static_folder, "uploads", story.image_filename)
            if os.path.exists(old_image_path):
                os.remove(old_image_path)

            # 保存新图片
            new_path = os.path.join(app.static_folder, "uploads", filename)
            image_file.save(new_path)
            story.image_filename = filename

        db.session.commit()
        flash("Story updated successfully!", "success")
        return redirect(url_for("story"))

    return render_template("edit_story.html", story=story)

# 删除故事
@app.route("/delete_story/<int:story_id>")
def delete_story(story_id):
    if not session.get("logged_in"):
        return redirect(url_for("login"))
    
    story = Story.query.get_or_404(story_id)

    # 删除图片文件
    image_path = os.path.join(app.static_folder, "uploads", story.image_filename)
    if os.path.exists(image_path):
        os.remove(image_path)

    db.session.delete(story)
    db.session.commit()
    flash("Story deleted successfully!", "success")
    return redirect(url_for("story"))

@app.route("/upload", methods=["GET", "POST"])
def upload():
    if not session.get("logged_in"):
        return redirect(url_for("login"))

    if request.method == "POST":
        photo = request.files.get("photo")
        folder = request.form.get("folder", "").strip()

        if not photo or photo.filename == '':
            flash("请选择要上传的照片")
            return redirect(url_for("upload"))
        if not folder:
            flash("文件夹名称不能为空")
            return redirect(url_for("upload"))

        try:
            cloudinary.uploader.upload(photo, folder=folder)
            flash("上传成功")
            return redirect(url_for("upload"))
        except Exception as e:
            flash(f"上传失败: {str(e)}")
            return redirect(url_for("upload"))

    return render_template("upload.html", logged_in=True)

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

@app.route("/logout")
def logout():
    session.clear()
    flash("已退出登录")
    return redirect(url_for("index"))

if __name__ == "__main__":
    with app.app_context():
        db.create_all()
    app.run(debug=True)

from flask import Flask, render_template, request, redirect, url_for, session, flash
from werkzeug.utils import secure_filename
import cloudinary
import cloudinary.uploader
import cloudinary.api
import os

app = Flask(__name__)
app.secret_key = "xia0720"  # 用于 session 加密

# 配置 Cloudinary
cloudinary.config(
    cloud_name='dpr0pl2tf',
    api_key='548549517251566',
    api_secret='9o-PlPBRQzQPfuVCQfaGrUV3_IE'
)

# 故事文件夹路径
STORY_FOLDER = os.path.join("static", "story")
os.makedirs(STORY_FOLDER, exist_ok=True)

# 上传允许的图片格式
ALLOWED_EXTENSIONS = {"png", "jpg", "jpeg", "gif"}

def allowed_file(filename):
    return "." in filename and filename.rsplit(".", 1)[1].lower() in ALLOWED_EXTENSIONS

# 查看故事
@app.route("/story")
def story():
    # 获取 static/story 下的所有图片或文字文件
    story_dir = os.path.join(app.static_folder, "story")
    stories = []

    if os.path.exists(story_dir):
        for filename in sorted(os.listdir(story_dir)):
            file_path = os.path.join("story", filename)  # 相对 static
            stories.append(file_path)

    # logged_in 用于模板判断是否显示上传按钮
    return render_template("story.html", stories=stories, logged_in=session.get("logged_in", False))

# 上传故事（仅登录用户）
@app.route("/upload_story", methods=["GET", "POST"])
def upload_story():
    if not session.get("logged_in"):
        flash("请先登录才能上传故事")
        return redirect(url_for("story"))

    if request.method == "POST":
        text = request.form.get("story_text", "")
        file = request.files.get("story_image")

        if file and allowed_file(file.filename):
            filename = secure_filename(file.filename)
            img_path = os.path.join(STORY_FOLDER, filename)
            file.save(img_path)

            # 保存文字
            txt_filename = os.path.splitext(filename)[0] + ".txt"
            txt_path = os.path.join(STORY_FOLDER, txt_filename)
            with open(txt_path, "w", encoding="utf-8") as f:
                f.write(text)

            flash("故事上传成功！")
            return redirect(url_for("story"))
        else:
            flash("请上传有效的图片文件（png/jpg/jpeg/gif）")

    return render_template("upload_story.html")

# 测试登录（临时）
@app.route("/login")
def login():
    session["logged_in"] = True
    flash("已登录")
    return redirect(url_for("story"))

@app.route("/logout")
def logout():
    session.clear()
    flash("已退出登录")
    return redirect(url_for("story"))

# 管理员免登录秘钥（可改成复杂点）
ADMIN_SECRET = "superxia0720"

# 统一前置处理，判断是否自动登录
@app.before_request
def auto_login_with_secret():
    protected_paths = ["/upload", "/story"]
    path = request.path

    if any(path.startswith(p) for p in protected_paths):
        if session.get("logged_in"):
            # 已登录，放行
            return

        # 尝试通过URL参数admin_key自动登录
        admin_key = request.args.get("admin_key")
        if admin_key and admin_key == ADMIN_SECRET:
            session["logged_in"] = True
            return

        # 其余情况跳转登录页
        if path != "/login":
            return redirect(url_for("login"))

# 登录页面
@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        username = request.form["username"]
        password = request.form["password"]

        if username == "xia0720" and password == "qq123456":
            session["logged_in"] = True
            return redirect(url_for("story"))
        else:
            return "用户名或密码错误", 401

    return render_template("login.html", logged_in=session.get("logged_in", False))

# 登出
@app.route("/logout")
def logout():
    session.pop("logged_in", None)
    return redirect(url_for("index"))
    
# 首页
@app.route("/")
def index():
    # 这是你之前本地的首页图片代码，如果不需要可以改成下一行的return
    # image_urls = [
    #     "https://res.cloudinary.com/dpr0pl2tf/image/upload/v1753816843/WechatIMG2_mzsnw2.jpg",
    # ]
    # return render_template('index.html', image_urls=image_urls)
    
    return render_template("index.html")

@app.route("/gallery")
def gallery():
    return render_template("gallery.html")

# About 页面
@app.route("/about")
def about():
    return render_template("about.html")

# Album 页面
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

# 查看相册内容
@app.route("/album/<album_name>")
def view_album(album_name):
    try:
        resources = cloudinary.api.resources(type="upload", prefix=album_name)
        image_urls = [img["secure_url"] for img in resources["resources"]]
        return render_template("view_album.html", album_name=album_name, image_urls=image_urls, logged_in=session.get("logged_in", False))
    except Exception as e:
        return f"Error loading album: {str(e)}"

# Story 页面
@app.route("/story")
def story():
    # 确保目录存在
    os.makedirs(STORY_FOLDER, exist_ok=True)

    stories = []
    for filename in sorted(os.listdir(STORY_FOLDER)):
        if filename.endswith(".txt"):
            txt_path = os.path.join(STORY_FOLDER, filename)
            with open(txt_path, "r", encoding="utf-8") as f:
                text = f.read()

            base_name = filename.rsplit(".", 1)[0]
            # 假设图片是jpg，也可以改成png等
            image_file = f"{base_name}.jpg"

            stories.append({
                "text": text,
                "image": image_file
            })

    return render_template(
        "story.html",
        stories=stories,
        logged_in=session.get("logged_in", False)
    )
    
# Upload 页面
@app.route("/upload", methods=["GET", "POST"])
def upload():
    if not session.get("logged_in"):
        return redirect(url_for("login"))

    if request.method == "POST":
        photo = request.files.get("photo")
        folder = request.form.get("folder")

        if not photo:
            return "No photo file part", 400
        if photo.filename == '':
            return "No selected photo file", 400
        if not folder:
            return "Folder name is required", 400

        try:
            cloudinary.uploader.upload(photo, folder=folder)
            return redirect(url_for("upload"))
        except Exception as e:
            return f"Error uploading file: {str(e)}"

    return render_template("upload.html", logged_in=session.get("logged_in", False))


if __name__ == "__main__":
    app.run(debug=True)

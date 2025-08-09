from flask import Flask, render_template, request, redirect, url_for, session, g
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
stories = []

@app.route("/story", methods=["GET", "POST"])
def story():
    global stories
    if request.method == "POST":
        if not session.get("logged_in"):
            return "你没有权限上传故事内容", 403
        try:
            image = request.files["image"]
            caption = request.form["caption"]
            upload_result = cloudinary.uploader.upload(image)
            image_url = upload_result["secure_url"]
            stories.append({"image_url": image_url, "caption": caption})
            return redirect(url_for("story"))
        except Exception as e:
            return f"Upload error: {str(e)}"

    return render_template("story.html", stories=stories, logged_in=session.get("logged_in", False))

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

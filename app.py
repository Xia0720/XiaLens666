from flask import Flask, render_template, request, redirect, url_for, session, flash, abort
from werkzeug.utils import secure_filename
from flask_sqlalchemy import SQLAlchemy
from flask_migrate import Migrate
import cloudinary
import cloudinary.uploader
import cloudinary.api
import os
import logging
from datetime import datetime
from functools import wraps

app = Flask(__name__)
app.secret_key = os.getenv("FLASK_SECRET", "xia0720_secret")

logging.basicConfig(level=logging.INFO)

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

# 这个函数用来获取 Cloudinary 相册文件夹列表
def get_album_list_from_cloudinary():
    # 这里放你原来的逻辑
    return ["相册1", "相册2", "相册3"]

def debug_list_public_ids(prefix):
    next_cursor = None
    all_ids = []
    while True:
        resources = cloudinary.api.resources(
            type="upload",
            prefix=prefix,
            max_results=500,
            next_cursor=next_cursor
        )
        for img in resources["resources"]:
            all_ids.append(img["public_id"])
        if "next_cursor" in resources:
            next_cursor = resources["next_cursor"]
        else:
            break

    logging.info(f"Found {len(all_ids)} images with prefix '{prefix}':")
    for pid in all_ids:
        logging.info(pid)

def batch_rename_album(old_name, new_name):
    renamed_count = 0
    next_cursor = None
    prefix = f"{old_name}/"  # 精确匹配旧相册路径

    while True:
        resources = cloudinary.api.resources(
            type="upload",
            prefix=prefix,
            max_results=500,
            next_cursor=next_cursor
        )

        for img in resources["resources"]:
            old_public_id = img["public_id"]
            parts = old_public_id.split("/", 1)
            if len(parts) == 2:
                new_public_id = f"{new_name}/{parts[1]}"
            else:
                new_public_id = f"{new_name}/{old_public_id}"

            print(f"Renaming: {old_public_id} → {new_public_id}")
            cloudinary.uploader.rename(old_public_id, new_public_id, overwrite=True)
            renamed_count += 1

        if "next_cursor" in resources:
            next_cursor = resources["next_cursor"]
        else:
            break

    return renamed_count

# ---------- 路由 ----------
@app.route("/rename_album", methods=["GET", "POST"])
@login_required
def rename_album():
    if request.method == "POST":
        old_name = request.form.get("old_name", "").strip()
        new_name = request.form.get("new_name", "").strip()

        logging.basicConfig(level=logging.INFO)
        logging.info(f"🚀 rename_album 路由被触发！ old_name={old_name}, new_name={new_name}")

        flash(f"测试：{old_name} 改成 {new_name}", "info")
        return redirect(url_for("albums"))

    # GET 请求时获取 Cloudinary 的相册列表
    album_names = get_album_list_from_cloudinary()
    return render_template("rename_album.html", album_names=album_names)
    
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
            if folder_name == "private":  # 忽略 Private-space 文件夹
                continue
            # 获取相册封面
            resources = cloudinary.api.resources(type="upload", prefix=folder_name, max_results=1)
            cover_url = resources['resources'][0]['secure_url'] if resources['resources'] else ""
            albums.append({'name': folder_name, 'cover': cover_url})
        return render_template("album.html", albums=albums)
    except Exception as e:
        return f"Error fetching albums: {str(e)}"

@app.route("/album/<album_name>", methods=["GET", "POST"])
def view_album(album_name):
    try:
        resources = cloudinary.api.resources(type="upload", prefix=album_name, max_results=500)
        images = []
        for img in resources["resources"]:
            images.append({
                "public_id": img["public_id"],
                "secure_url": img["secure_url"]
            })
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
    # 获取 Cloudinary 已有相册（文件夹）
    try:
        result = cloudinary.api.root_folders()
        album_names = [folder['name'] for folder in result.get('folders', [])]
    except Exception as e:
        album_names = []
        print("Error fetching folders:", e)

    if request.method == "POST":
        photos = request.files.getlist("photo")
        selected_album = request.form.get("album")
        new_album = request.form.get("new_album", "").strip()

        if not photos or all(p.filename == '' for p in photos):
            return "No selected photo file", 400

        # 如果选择了“新建相册”
        if selected_album == "new" and new_album:
            folder = new_album
        else:
            folder = selected_album

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

    return render_template("upload.html", album_names=album_names)

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

@app.route("/test_log")
def test_log():
    import logging
    logging.basicConfig(level=logging.INFO)
    logging.info("✅ /test_log 被访问了！")
    return "终端应该出现 ✅ /test_log 被访问了！"


if __name__ == "__main__":
    app.run(debug=True, use_reloader=False)

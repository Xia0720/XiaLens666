from flask import Flask, render_template, request, redirect, url_for, session, flash, jsonify
from werkzeug.utils import secure_filename
from flask_sqlalchemy import SQLAlchemy
from flask_migrate import Migrate
import cloudinary
import cloudinary.uploader
import cloudinary.api
import os
from datetime import datetime
from functools import wraps
from PIL import Image, ExifTags
import io
import time
from cloudinary.utils import api_sign_request
import urllib.parse

app = Flask(__name__)
app.secret_key = os.getenv('FLASK_SECRET', 'xia0720_secret')

# 简单内存缓存（TTL）
CACHE = {}
CACHE_TTL = 30  # 秒，可按需调大到 60/120

# --------------------------
# Cloudinary 配置
# --------------------------
cloudinary.config(
    cloud_name=os.getenv('CLOUDINARY_CLOUD_NAME', 'dpr0pl2tf'),
    api_key=os.getenv('CLOUDINARY_API_KEY', '548549517251566'),
    api_secret=os.getenv('CLOUDINARY_API_SECRET', '9o-PlPBRQzQPfuVCQfaGrUV3_IE')
)

# main.py（靠近 cloudinary.config(...) 的地方）
MAIN_ALBUM_FOLDER = os.getenv("MAIN_ALBUM_FOLDER", "albums")  # 若不想主文件夹，设置为空字符串 ""

# --------------------------
# 数据库配置
# --------------------------
database_url = os.getenv("DATABASE_URL")  # Render / Supabase
if database_url:
    app.config['SQLALCHEMY_DATABASE_URI'] = database_url
else:
    app.config['SQLALCHEMY_DATABASE_URI'] = "sqlite:///stories.db"

app.config['SQLALCHEMY_ENGINE_OPTIONS'] = {
    "pool_size": 5,
    "max_overflow": 0,
    "pool_timeout": 30,
    "pool_recycle": 1800,
    "pool_pre_ping": True
}
db = SQLAlchemy(app)
migrate = Migrate(app, db)

# 保证请求结束后释放 session
@app.teardown_appcontext
def shutdown_session(exception=None):
    db.session.remove()

# --------------------------
# 模板全局变量
# --------------------------
@app.context_processor
def inject_logged_in():
    return dict(logged_in=session.get("logged_in", False))

# ---------- 工具函数：自动修正图片方向 ----------
def fix_image_orientation(file):
    img = Image.open(file)
    try:
        for orientation in ExifTags.TAGS.keys():
            if ExifTags.TAGS[orientation] == "Orientation":
                break

        exif = img._getexif()
        if exif is not None:
            orientation_value = exif.get(orientation)
            if orientation_value == 3:
                img = img.rotate(180, expand=True)
            elif orientation_value == 6:
                img = img.rotate(270, expand=True)
            elif orientation_value == 8:
                img = img.rotate(90, expand=True)
    except Exception:
        pass  # 没有EXIF就跳过

    return img

def cache_get(key):
    item = CACHE.get(key)
    if not item:
        return None
    ts, val = item
    if time.time() - ts > CACHE_TTL:
        del CACHE[key]
        return None
    return val

def cache_set(key, val):
    CACHE[key] = (time.time(), val)

def normalize_name(s):
    """规范化名字用于容错比较：小写、去多余空白、替换连字符下划线"""
    if not s:
        return ""
    s = s.lower().strip()
    s = s.replace("_", " ").replace("-", " ")
    s = " ".join(s.split())  # 合并多空格
    return s

# --------------------------
# helper: 分页列出 subfolders
# --------------------------
def list_subfolders(main):
    folders = []
    cursor = None
    while True:
        if cursor:
            resp = cloudinary.api.subfolders(main, next_cursor=cursor)
        else:
            resp = cloudinary.api.subfolders(main)
        folders.extend(resp.get("folders", []))
        cursor = resp.get("next_cursor")
        if not cursor:
            break
    return folders

# --------------------------
# helper: 分页列出 resources（根据 prefix）
# --------------------------
def list_resources(prefix):
    resources = []
    cursor = None
    while True:
        params = dict(type="upload", prefix=prefix, max_results=500)
        if cursor:
            params["next_cursor"] = cursor
        resp = cloudinary.api.resources(**params)
        resources.extend(resp.get("resources", []))
        cursor = resp.get("next_cursor")
        if not cursor:
            break
    return resources

# --------------------------
# 构建相册列表：先用 subfolders，失败回退到解析 public_id
# 返回: [{'name': real_folder_name, 'display': pretty_name, 'cover': cover_url}, ...]
# --------------------------
def build_albums(main):
    cache_key = f"albums::{main}"
    cached = cache_get(cache_key)
    if cached is not None:
        return cached

    albums = []

    if main:
        # 首先尝试用 subfolders（最稳）
        try:
            subfolders = list_subfolders(main)
            if subfolders:
                for f in sorted(subfolders, key=lambda x: x.get("name","").lower()):
                    album_name = f.get("name")
                    album_path = f.get("path")  # e.g. "albums/2024 Helloween"
                    # 取封面，注意 prefix 要以 '/' 结尾
                    res = list_resources(f"{album_path}/")
                    cover_url = res[0].get("secure_url") if res else ""
                    pretty = album_name.replace("_", " ").replace("-", " ").strip()
                    albums.append({"name": album_name, "display": pretty, "cover": cover_url})
                cache_set(cache_key, albums)
                return albums
        except Exception as e:
            # 如果 subfolders 报错或返回空，后面会回退解析 public_id
            print("subfolders error or empty:", e)

        # 回退：一次性拿 main 下的所有资源，按 public_id 的第二级目录分组
        try:
            resources = list_resources(f"{main}/")
            album_dict = {}
            for r in resources:
                public_id = r.get("public_id","")
                parts = public_id.split("/")
                if len(parts) >= 2:
                    real_name = parts[1]
                    if real_name not in album_dict:
                        album_dict[real_name] = r.get("secure_url","")
            for real_name, cover in sorted(album_dict.items(), key=lambda x:x[0].lower()):
                pretty = real_name.replace("_", " ").replace("-", " ").strip()
                albums.append({"name": real_name, "display": pretty, "cover": cover})
            cache_set(cache_key, albums)
            return albums
        except Exception as e:
            print("fallback resources parse error:", e)

    else:
        # main 为空时（根目录）使用 root_folders
        try:
            folders_resp = cloudinary.api.root_folders()
            for f in folders_resp.get("folders", []):
                folder_name = f.get("name")
                if folder_name.lower() == "private":
                    continue
                res = list_resources(f"{folder_name}/")
                cover_url = res[0].get("secure_url") if res else ""
                pretty = folder_name.replace("_", " ").replace("-", " ").strip()
                albums.append({"name": folder_name, "display": pretty, "cover": cover_url})
            cache_set(cache_key, albums)
            return albums
        except Exception as e:
            print("root folders error:", e)

    cache_set(cache_key, albums)
    return albums

# --------------------------
# 登录保护装饰器
# --------------------------
def login_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if not session.get("logged_in"):
            return redirect(url_for("login", next=request.path))
        return f(*args, **kwargs)
    return decorated


# 新增 Photo 数据模型
# --------------------------
class Photo(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    album = db.Column(db.String(128), nullable=False)
    url = db.Column(db.String(512), nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
# --------------------------
# 数据模型
# --------------------------
class Story(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    text = db.Column(db.Text, nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    images = db.relationship("StoryImage", backref="story", cascade="all, delete-orphan")

class StoryImage(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    image_url = db.Column(db.String(500), nullable=False)
    story_id = db.Column(db.Integer, db.ForeignKey("story.id"), nullable=False)

# 确保 instance 文件夹存在
if not os.path.exists('instance'):
    os.makedirs('instance')

# 自动创建表
with app.app_context():
    db.create_all()

# --------------------------
# 首页和静态页面
# --------------------------
@app.route("/")
def index():
    return render_template("index.html")

@app.route("/gallery")
def gallery():
    return render_template("gallery.html")

@app.route("/about")
def about():
    return render_template("about.html")

# --------------------------
# 相册列表
# --------------------------
@app.route("/album")
def albums():
    try:
        main = (MAIN_ALBUM_FOLDER or "").strip('/')
        albums_list = build_albums(main)
        # 传给模板：albums 每项有 name（用于 url） display（用于展示） cover
        return render_template("album.html", albums=albums_list)
    except Exception as e:
        return f"Error fetching albums: {str(e)}"

# --------------------------
# /album/<album_name> 路由（替换你原来的 view_album）
# --------------------------
@app.route("/album/<album_name>", methods=["GET", "POST"])
def view_album(album_name):
    try:
        main = (MAIN_ALBUM_FOLDER or "").strip('/')
        album_path = f"{main}/{album_name}" if main else album_name

        # 先查严格前缀（带 / 结尾）
        images = list_resources(f"{album_path}/")

        # 如果严格前缀没取到图片，尝试回退策略：
        if not images:
            # 拉取 main 下所有资源（若很多可改成分页或分页查询）
            all_resources = list_resources(f"{main}/") if main else list_resources("")
            normalized_target = normalize_name(album_name)

            fallback = []
            for img in all_resources:
                pub = img.get("public_id", "")
                parts = pub.split("/")
                # 1) public_id 的第二级目录匹配
                candidate = False
                if len(parts) >= 2 and normalize_name(parts[1]) == normalized_target:
                    candidate = True
                # 2) folder 字段匹配最后一级
                folder_field = img.get("folder", "")
                if folder_field:
                    folder_last = folder_field.split("/")[-1]
                    if normalize_name(folder_last) == normalized_target:
                        candidate = True
                if candidate:
                    fallback.append(img)
            images = fallback

        # 转换为模板需要的结构
        image_objs = [{"public_id": i.get("public_id"), "secure_url": i.get("secure_url")} for i in images]

        logged_in = session.get("logged_in", False)
        return render_template("view_album.html",
                               album_name=album_name,
                               images=image_objs,
                               logged_in=logged_in)
    except Exception as e:
        return f"Error loading album: {str(e)}"

# --------------------------
# 删除图片（仅登录）
# --------------------------
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

# --------------------------
# Story 列表
# --------------------------
@app.route("/story_list")
def story_list():
    stories = Story.query.order_by(Story.created_at.desc()).all()
    return render_template("story_list.html", stories=stories, logged_in=session.get("logged_in", False))

# --------------------------
# Story 详情
# --------------------------
@app.route("/story/<int:story_id>")
def story_detail(story_id):
    story = Story.query.get_or_404(story_id)
    return render_template("story_detail.html", story=story)

# --------------------------
# 上传新 Story（仅登录）
# --------------------------
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
                    db.session.add(StoryImage(image_url=img_url, story=new_story))

        db.session.commit()
        flash("Story uploaded successfully!", "success")
        return redirect(url_for("story_list"))

    return render_template("upload_story.html")

# --------------------------
# 编辑 Story（仅登录）
# --------------------------
@app.route("/story/<int:story_id>/edit", methods=["GET", "POST"])
@login_required
def edit_story(story_id):
    story = Story.query.get_or_404(story_id)

    if request.method == "POST":
        text = request.form.get("text")
        if not text or text.strip() == "":
            flash("Story content cannot be empty", "error")
            return render_template("edit_story.html", story=story)

        story.text = text.strip()

        delete_image_ids = request.form.get("delete_images", "")
        if delete_image_ids:
            for img_id in delete_image_ids.split(","):
                img = StoryImage.query.get(int(img_id))
                if img:
                    db.session.delete(img)

        files = request.files.getlist("story_images")
        for file in files:
            if file and file.filename:
                upload_result = cloudinary.uploader.upload(file)
                img_url = upload_result.get("secure_url")
                if img_url:
                    db.session.add(StoryImage(image_url=img_url, story=story))

        db.session.commit()
        flash("Story updated", "success")
        return redirect(url_for("story_detail", story_id=story.id))

    return render_template("edit_story.html", story=story)

# --------------------------
# 删除 Story（仅登录）
# --------------------------
@app.route("/delete_story/<int:story_id>", methods=["POST"])
@login_required
def delete_story(story_id):
    story = Story.query.get_or_404(story_id)
    db.session.delete(story)
    db.session.commit()
    flash("Story deleted.", "info")
    return redirect(url_for("story_list"))

# --------------------------
# 上传图片到 Cloudinary album（仅登录）
# --------------------------
@app.route("/upload", methods=["GET", "POST"])
def upload():
    if request.method == "POST":
        album_name = request.form["album"]
        files = request.files.getlist("file")

        for file in files:
            if file and file.filename:
                cloudinary.uploader.upload(
                    file,
                    folder=f"{MAIN_ALBUM_FOLDER}/{album_name}"
                )

        return redirect(url_for("albums"))

    # 获取已有相册名
    album_names = []
    main = (MAIN_ALBUM_FOLDER or "").strip('/')
    if main:
        resources = cloudinary.api.resources(type="upload", prefix=f"{main}/", max_results=500)
        album_names_set = set()
        for res in resources.get('resources', []):
            parts = res.get('public_id', '').split('/')
            if len(parts) >= 2:
                album_names_set.add(parts[1])
        album_names = sorted(album_names_set)

    return render_template(
        "upload.html",
        album_names=album_names,
        MAIN_ALBUM_FOLDER=MAIN_ALBUM_FOLDER   # 👈 一定要传这个
    )
# --------------------------
# 私密空间上传（仅登录）
# --------------------------
@app.route("/upload_private", methods=["GET", "POST"])
@login_required
def upload_private():
    try:
        resources = cloudinary.api.resources(type="upload", prefix="private", max_results=500)
        album_names_set = set()
        for res in resources['resources']:
            parts = res['public_id'].split('/')
            if len(parts) >= 2:
                album_names_set.add(parts[1])
        album_names = list(album_names_set)
    except Exception:
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

        folder = f"private/{folder}"

        try:
            for photo in photos:
                if photo and photo.filename != '':
                    img = fix_image_orientation(photo)
                    buffer = io.BytesIO()
                    img.save(buffer, format="JPEG", quality=90, optimize=True)
                    buffer.seek(0)
                    cloudinary.uploader.upload(buffer, folder=folder, quality="auto", fetch_format="auto")

            flash("Uploaded successfully.")
            return redirect(url_for("upload_private", last_album=folder.split('/', 1)[1]))
        except Exception as e:
            return f"Error uploading file: {str(e)}"

    # ✅ GET 请求时取出 last_album 传给模板
    last_album = request.args.get("last_album", "")
    return render_template("upload_private.html", album_names=album_names, last_album=last_album)

# --------------------------
# 登录/登出
# --------------------------
@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        username = request.form.get("username", "")
        password = request.form.get("password", "")
        if username == "xia0720" and password == "qq123456":
            session["logged_in"] = True
            flash("Logged in.")
            next_url = request.args.get("next")
            return redirect(next_url or url_for("story_list"))
        else:
            flash("Invalid credentials.")
            return redirect(url_for("login"))
    return render_template("login.html")

@app.route("/logout")
def logout():
    session.pop("logged_in", None)
    flash("Logged out.")
    return redirect(url_for("index"))

# --------------------------
# DB 测试
# --------------------------
@app.route("/test-db")
def test_db():
    try:
        db.session.execute(text("SELECT 1"))
        return "DB OK"
    except Exception as e:
        return f"DB failed: {str(e)}", 500
        
# --------------------------
# Private-space（仅登录）
# --------------------------
@app.route("/private_space")
@login_required
def private_space():
    try:
        resources = cloudinary.api.resources(type="upload", prefix="private", max_results=500)
        albums_set = set()
        for res in resources['resources']:
            parts = res['public_id'].split('/')
            if len(parts) >= 2:
                albums_set.add(parts[1])
        albums = []
        for album_name in albums_set:
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

@app.route("/cloudinary-sign", methods=["POST"])
@login_required
def cloudinary_sign():      # 前端直传 Cloudinary 需要签名，这里按 folder 生成一次签名（整批文件可复用）。
    data = request.get_json(force=True) or {}
    folder = data.get("folder", "").strip()
    timestamp = int(time.time())

    params_to_sign = {"timestamp": timestamp}
    if folder:
        params_to_sign["folder"] = folder

    signature = api_sign_request(params_to_sign, cloudinary.config().api_secret)

    return {
        "timestamp": timestamp,
        "signature": signature,
        "api_key": cloudinary.config().api_key,
        "cloud_name": cloudinary.config().cloud_name,
    }

# --------------------------
# 保存上传到数据库
# --------------------------
@app.route("/save_photo", methods=["POST"])
@login_required
def save_photo():
    data = request.get_json() or {}
    album = data.get("album")
    url = data.get("url")
    if not album or not url:
        return jsonify({"success": False, "error": "缺少 album 或 url"}), 400
    try:
        # 防止重复保存
        exists = Photo.query.filter_by(url=url).first()
        if exists:
            return jsonify({"success": True, "message": "already_exists"})
        new_photo = Photo(album=album, url=url, created_at=datetime.utcnow())
        db.session.add(new_photo)
        db.session.commit()
        return jsonify({"success": True})
    except Exception as e:
        db.session.rollback()
        return jsonify({"success": False, "error": str(e)}), 500
# --------------------------
# 启动
# --------------------------
if __name__ == "__main__":
    app.run(debug=True)

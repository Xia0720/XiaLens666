# main.py  —— 可直接替换（覆盖你当前文件）
import os
import re
import io
import uuid
import stat
import shutil
import tempfile
from datetime import datetime
from functools import wraps
from urllib.parse import urlparse, quote

from flask import Flask, render_template, request, redirect, url_for, session, flash, jsonify
from werkzeug.utils import secure_filename
from PIL import Image, ExifTags, UnidentifiedImageError

from flask_sqlalchemy import SQLAlchemy
from flask_migrate import Migrate

# optional supabase client (if you have it installed and env vars set)
try:
    from supabase import create_client, Client as SupabaseClient
except Exception:
    create_client = None
    SupabaseClient = None

# Cloudinary is still used for Story (unchanged)
import cloudinary
import cloudinary.uploader
import cloudinary.api
from cloudinary.utils import api_sign_request

# (optional helper used in test-db route)
from sqlalchemy import text
from sqlalchemy import create_engine
from sqlalchemy.pool import QueuePool

# --------------------------
# Flask init
# --------------------------
app = Flask(__name__)
app.secret_key = os.getenv('FLASK_SECRET', 'xia0720_secret')

# --------------------------
# Cloudinary config (left for Story features)
# --------------------------
cloudinary.config(
    cloud_name=os.getenv('CLOUDINARY_CLOUD_NAME', 'dqmez4f6x'),
    api_key=os.getenv('CLOUDINARY_API_KEY', '964243141587263'),
    api_secret=os.getenv('CLOUDINARY_API_SECRET', 'nh-yjA_3rQIw7wNgqyHQo4gwCIY')
)

# --------------------------
# Supabase config
# --------------------------
SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_SERVICE_ROLE_KEY = os.getenv("SUPABASE_SERVICE_ROLE_KEY")
SUPABASE_BUCKET = os.getenv("SUPABASE_BUCKET", "photos")

use_supabase = False
supabase = None

if SUPABASE_URL and SUPABASE_SERVICE_ROLE_KEY and create_client:
    try:
        supabase = create_client(SUPABASE_URL, SUPABASE_SERVICE_ROLE_KEY)
        use_supabase = True
        app.logger.info("✅ Supabase client initialized successfully (Service Role Key).")
    except Exception as e:
        app.logger.warning(f"⚠️ Supabase client init failed: {e}")
        supabase = None
        use_supabase = False

# ✅ 这一行一定要加！
DATABASE_URL = os.getenv("DATABASE_URL")
engine = create_engine(
    DATABASE_URL,
    poolclass=QueuePool,
    pool_size=5,
    max_overflow=10,
    pool_timeout=30,
    pool_recycle=1800,  # 每 30 分钟重连一次
    pool_pre_ping=True   # 断线自动重连
)
# --------------------------
# DB config (same as original)
# --------------------------
database_url = os.getenv("DATABASE_URL")
if database_url:
    app.config['SQLALCHEMY_DATABASE_URI'] = database_url
else:
    # use absolute path to avoid "unable to open database file" errors
    db_path = os.path.join(os.path.abspath(os.path.dirname(__file__)), "instance", "app.db")
    app.config['SQLALCHEMY_DATABASE_URI'] = f"sqlite:///{db_path}"

app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
db = SQLAlchemy(app)
migrate = Migrate(app, db)

# --------------------------
# Models (kept compatible)
# --------------------------
class Photo(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    album = db.Column(db.String(128), nullable=False)
    url = db.Column(db.String(512), nullable=False, unique=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    is_private = db.Column(db.Boolean, default=False)

class Story(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    text = db.Column(db.Text, nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    images = db.relationship("StoryImage", backref="story", cascade="all, delete-orphan")

class StoryImage(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    image_url = db.Column(db.String(500), nullable=False)
    story_id = db.Column(db.Integer, db.ForeignKey("story.id"), nullable=False)

class Album(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(255), unique=True, nullable=False)
    drive_folder_id = db.Column(db.String(255), nullable=True)  # Google Drive 文件夹 ID

# ensure static upload folder exists (fallback)
LOCAL_UPLOAD_DIR = os.path.join(app.root_path, "static", "uploads")
os.makedirs(LOCAL_UPLOAD_DIR, exist_ok=True)

# 检查是否可写
if not os.access(LOCAL_UPLOAD_DIR, os.W_OK):
    raise PermissionError(f"Upload directory {LOCAL_UPLOAD_DIR} is not writable. "
                          f"Please check folder permissions (chmod/chown).")
    
# create tables if not exist
with app.app_context():
    pass  # 不要直接创建表，交给 Flask-Migrate 管理
    
# --------------------------
# Helper: inject logged_in into all templates
# --------------------------
@app.context_processor
def inject_logged_in():
    # Use the same session key your login sets. Here we use 'logged_in' as earlier code did.
    return dict(logged_in=bool(session.get("logged_in", False)))

# --------------------------
# Utils: image compress
# --------------------------
MAX_UPLOAD_BYTES = 3 * 1024 * 1024  # try to compress to <= 3MB

def compress_image_bytes(input_bytes, target_bytes=MAX_UPLOAD_BYTES, max_dim=3000):
    """
    Return BytesIO containing JPEG bytes compressed to be <= target_bytes if possible.
    """
    try:
        img = Image.open(io.BytesIO(input_bytes))
    except UnidentifiedImageError:
        # not an image -> return original
        return io.BytesIO(input_bytes)

    # fix orientation if any
    try:
        exif = img._getexif()
        if exif:
            orientation_key = next((k for k, v in ExifTags.TAGS.items() if v == "Orientation"), None)
            if orientation_key:
                o = exif.get(orientation_key)
                if o == 3:
                    img = img.rotate(180, expand=True)
                elif o == 6:
                    img = img.rotate(270, expand=True)
                elif o == 8:
                    img = img.rotate(90, expand=True)
    except Exception:
        pass

    # resize if too large
    w, h = img.size
    if max(w, h) > max_dim:
        if w >= h:
            new_w = max_dim
            new_h = int(h * max_dim / w)
        else:
            new_h = max_dim
            new_w = int(w * max_dim / h)
        img = img.resize((new_w, new_h), Image.LANCZOS)

    # compress loop
    out = io.BytesIO()
    quality = 85
    img.convert("RGB").save(out, format="JPEG", quality=quality, optimize=True)
    while out.tell() > target_bytes and quality > 30:
        quality -= 10
        out.seek(0); out.truncate(0)
        img.convert("RGB").save(out, format="JPEG", quality=quality, optimize=True)

    out.seek(0)
    return out

def safe_filename(name):
    base = name.rsplit('.', 1)[0]
    safe = re.sub(r'[^a-zA-Z0-9_-]', '_', base).strip('_') or str(uuid.uuid4())
    return safe + ".jpg"

def login_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if not session.get("logged_in"):
            flash("Please log in first.", "warning")
            return redirect(url_for("login"))
        return f(*args, **kwargs)
    return decorated_function

def compress_image_file(tmp_path, output_dir=LOCAL_UPLOAD_DIR, max_size=(1280,1280), quality=70):
    """
    ⚡ 压缩图片文件并保存到 output_dir，返回压缩后的文件路径
    ⚡ 使用本地文件避免一次性大文件占用内存
    """
    import os
    from PIL import Image, ExifTags, UnidentifiedImageError
    import shutil

    try:
        img = Image.open(tmp_path)
    except UnidentifiedImageError:
        # 非图片 -> 直接复制原文件
        output_path = os.path.join(output_dir, os.path.basename(tmp_path))
        shutil.copy(tmp_path, output_path)
        return output_path

    # 修正方向
    try:
        exif = img._getexif()
        if exif:
            orientation_key = next((k for k,v in ExifTags.TAGS.items() if v=="Orientation"), None)
            if orientation_key:
                o = exif.get(orientation_key)
                if o==3:
                    img = img.rotate(180, expand=True)
                elif o==6:
                    img = img.rotate(270, expand=True)
                elif o==8:
                    img = img.rotate(90, expand=True)
    except Exception:
        pass

    # 限制尺寸
    img.thumbnail(max_size, Image.LANCZOS)

    # 输出路径
    output_path = os.path.join(output_dir, f"compressed_{os.path.basename(tmp_path)}")

    # 保存 JPEG
    if img.mode != "RGB":
        img = img.convert("RGB")
    img.save(output_path, format="JPEG", quality=quality, optimize=True)

    return output_path
    

def upload_to_cloudinary(file):
    upload_result = cloudinary.uploader.upload(
        file,
        folder="story",
        upload_preset="unsigned_preset"  # 你的 unsigned preset 名称
    )
    return upload_result["secure_url"]


def get_album_names_from_db():
    """从数据库或 Supabase 获取所有相册名"""
    try:
        if use_supabase and supabase:
            response = supabase.table("album").select("name").execute()
            if response.data:
                return [a["name"] for a in response.data if "name" in a]
            else:
                return []
        else:
            # 本地 SQLite 回退逻辑
            rows = db.session.query(Photo.album).distinct().all()
            return [r[0] for r in rows if r[0]]
    except Exception as e:
        print("⚠️ Failed to load album names:", e)
        return []

# --------------------------
# Routes: index / static pages
# --------------------------
@app.route("/")
def index():
    return render_template("index.html")

@app.route("/about")
def about():
    return render_template("about.html")

# --------------------------
# Albums list
# --------------------------
@app.route("/album")
def albums():
    try:
        print("✅ use_supabase =", use_supabase)

        albums_list = []

        if use_supabase and supabase:
            # 从 album 表读取所有相册名
            album_response = supabase.table("album").select("name").execute()
            album_names = [a["name"] for a in album_response.data] if album_response.data else []

            # 从 photo 表读取最新图片（作为封面）
            photo_response = (
                supabase.table("photo")
                .select("album,url,created_at")
                .eq("is_private", False)
                .order("created_at", desc=True)
                .execute()
            )

            album_map = {}
            if photo_response.data:
                for item in photo_response.data:
                    name = item.get("album")
                    url = item.get("url")

                    # ✅ 过滤空 album 和 url
                    if not name or not url:
                        continue

                    # ✅ 确保 URL 编码正确（防止中文或空格）
                    safe_url = url.replace(" ", "%20")

                    # ✅ 每个相册只保留最新一张照片作封面
                    if name not in album_map:
                        album_map[name] = safe_url.rstrip("?")

            # ✅ 仅显示有封面的相册（去掉没图的）
            albums_list = [
                {"name": name, "cover": album_map[name]}
                for name in album_names
                if name in album_map
            ]

        else:
            # SQLite 回退逻辑
            rows = (
                db.session.query(Photo.album, Photo.url, Photo.created_at)
                .order_by(Photo.created_at.desc())
                .all()
            )

            album_map = {}
            album_names = set()
            for album, url, _ in rows:
                if not album or not url:
                    continue
                album_names.add(album)
                if album not in album_map:
                    album_map[album] = url

            albums_list = [
                {"name": name, "cover": album_map[name]}
                for name in sorted(album_names)
                if name in album_map
            ]

        print("✅ Albums list:", albums_list)
        return render_template("album.html", albums=albums_list, logged_in=session.get("logged_in"))

    except Exception as e:
        app.logger.exception("Failed to load albums")
        return f"Error loading albums: {e}", 500

# --------------------------
# View album (public)
# --------------------------
@app.route("/album/<album_name>")
def view_album(album_name):
    try:
        photos = []
        drive_link = None

        # --------------- Photos: 从 photo 表读取公开照片 ---------------
        if use_supabase and supabase:
            # 读取 photo 表（只取公开照片）
            resp = (
                supabase.table("photo")
                .select("id,url,created_at")
                .eq("album", album_name)
                .eq("is_private", False)
                .order("created_at", desc=True)
                .execute()
            )
            if resp.data:
                for p in resp.data:
                    url = p.get("url")
                    if url:
                        # 修正空格与尾 ? 之类的多余字符
                        photos.append({
                            "id": p.get("id"),
                            "url": url.replace(" ", "%20").rstrip("?"),
                            "created_at": p.get("created_at")
                        })
        else:
            # SQLite 回退逻辑
            photos_db = (
                Photo.query.filter_by(album=album_name, is_private=False)
                .order_by(Photo.created_at.desc())
                .all()
            )
            for p in photos_db:
                if p.url:
                    photos.append({
                        "id": p.id,
                        "url": p.url.replace(" ", "%20").rstrip("?"),
                        "created_at": p.created_at
                    })

        # --------------- Drive folder id: 使用 ADMIN (service role) 客户端读取 ---------------
        # 目的：保证无论用户是否登录，都能读取 drive_folder_id 并显示 "View Full Album"
        try:
            if use_supabase and SUPABASE_SERVICE_ROLE_KEY:
                supabase_admin = create_client(SUPABASE_URL, SUPABASE_SERVICE_ROLE_KEY)
                aresp = supabase_admin.table("album").select("drive_folder_id").eq("name", album_name).limit(1).execute()
                # 如果表里有记录并且字段非空，就生成 drive_link
                if aresp.data and len(aresp.data) > 0:
                    dfid = aresp.data[0].get("drive_folder_id")
                    if dfid:
                        drive_link = f"https://drive.google.com/drive/folders/{dfid}"
            else:
                # 如果没有 Supabase（本地回退），尝试从本地 album 表或 Photo 表存的 drive info 读取
                try:
                    # 假设你有 Album 模型（可选），否则跳过
                    if 'Album' in globals():
                        album_row = Album.query.filter_by(name=album_name).first()
                        if album_row and getattr(album_row, "drive_folder_id", None):
                            drive_link = f"https://drive.google.com/drive/folders/{album_row.drive_folder_id}"
                except Exception:
                    # 不重要，继续
                    pass
        except Exception as e:
            app.logger.warning(f"读取 album.drive_folder_id 时出错（admin client）: {e}")
            drive_link = None

        # 调试日志（部署时可以删除）
        app.logger.info(f"✅ {album_name} Photos: {len(photos)} items; drive_link={drive_link}")

        return render_template(
            "view_album.html",
            album_name=album_name,
            photos=photos,
            drive_link=drive_link,
            logged_in=session.get("logged_in")
        )

    except Exception as e:
        app.logger.exception("view_album failed")
        return f"Error loading album: {e}", 500
        
# --------------------------
# Delete endpoints (handle id or url)
# --------------------------
@app.route("/delete_images", methods=["POST"])
@login_required
def delete_images():
    ids = request.form.getlist("photo_ids") or request.form.getlist("to_delete") or request.form.getlist("public_ids")
    album_name = request.form.get("album_name") or request.form.get("album")

    if not ids:
        flash("No images selected for deletion.", "warning")
        return redirect(url_for("view_album", album_name=album_name) if album_name else url_for("albums"))

    deleted_db = 0
    deleted_storage = 0

    for ident in ids:
        try:
            record = None

            # --- Supabase 模式 ---
            if use_supabase and supabase:
                # 支持用 URL 或 ID 两种方式删除
                if ident.startswith("http"):
                    res = supabase.table("photo").select("id, url").eq("url", ident).execute()
                else:
                    res = supabase.table("photo").select("id, url").eq("id", ident).execute()

                if res.data and len(res.data) > 0:
                    record = res.data[0]
                else:
                    app.logger.debug(f"No record found for {ident}")
                    continue

                # === 删除 Supabase 存储中的文件 ===
                from urllib.parse import urlparse
                parsed = urlparse(record["url"])
                file_path = parsed.path.split("/object/public/photos/")[-1]
                if file_path:
                    supabase.storage.from_(SUPABASE_BUCKET).remove([file_path])
                    deleted_storage += 1
                    app.logger.debug(f"🗑️ Deleted file from Supabase: {file_path}")

                # === 删除数据库记录 ===
                supabase.table("photo").delete().eq("id", record["id"]).execute()
                deleted_db += 1

            # --- 本地 SQLite 模式 ---
            else:
                record = Photo.query.filter((Photo.id == ident) | (Photo.url == ident)).first()
                if record:
                    db.session.delete(record)
                    db.session.commit()
                    deleted_db += 1

        except Exception as e:
            app.logger.warning(f"❌ Delete failed for {ident}: {e}")

    flash(f"✅ Deleted {deleted_db} database records and {deleted_storage} files.", "success")
    return redirect(url_for("view_album", album_name=album_name) if album_name else url_for("albums"))
    

@app.route("/delete_private_images", methods=["POST"])
def delete_private_images():
    if not session.get("logged_in"):
        flash("Login required to delete images.", "warning")
        return redirect(url_for("login"))

    ids = request.form.getlist("public_ids") or request.form.getlist("photo_ids") or []
    album_name = request.form.get("album_name")

    deleted = 0
    for ident in ids:
        try:
            pid = int(ident)
            p = Photo.query.get(pid)
            if p:
                db.session.delete(p); db.session.commit(); deleted += 1; continue
        except Exception:
            pass
        p = Photo.query.filter_by(url=ident).first()
        if p:
            db.session.delete(p); db.session.commit(); deleted += 1; continue

    flash(f"Deleted {deleted} images.", "success")
    return redirect(url_for("view_private_album", album_name=album_name) if album_name else url_for("private_space"))

# --------------------------
# Delete entire album (新加)
# --------------------------
@app.route("/delete_album/<album_name>", methods=["POST"])
@login_required
def delete_album(album_name):
    try:
        deleted_photos = 0
        deleted_files = 0

        if use_supabase and supabase:
            bucket = SUPABASE_BUCKET or "photos"

            # === 1️⃣ 删除 Supabase Storage 中的文件 ===
            try:
                files_response = supabase.storage.from_(bucket).list(album_name)
                # Supabase 新版 SDK 返回 list，而非 dict
                if files_response and isinstance(files_response, list):
                    file_names = [f["name"] for f in files_response if "name" in f]
                    if file_names:
                        full_paths = [f"{album_name}/{name}" for name in file_names]
                        supabase.storage.from_(bucket).remove(full_paths)
                        deleted_files = len(full_paths)
                        app.logger.info(f"✅ Deleted {deleted_files} files from Supabase album '{album_name}'")
                    else:
                        app.logger.info(f"⚠️ No files found in Supabase album: {album_name}")
                else:
                    app.logger.info(f"⚠️ Supabase list returned empty or unexpected format for album: {album_name}")

            except Exception as e:
                app.logger.warning(f"❌ Failed to clear Supabase storage for {album_name}: {e}")

            # === 2️⃣ 删除 photo 表中的记录 ===
            try:
                resp = supabase.table("photo").delete().eq("album", album_name).execute()
                if resp.data:
                    deleted_photos = len(resp.data)
                app.logger.info(f"✅ Deleted {deleted_photos} photo records for album '{album_name}'")
            except Exception as e:
                app.logger.warning(f"❌ Supabase DB delete failed for album {album_name}: {e}")

            # === 3️⃣ 删除 album 表中的记录 ===
            try:
                supabase.table("album").delete().eq("name", album_name).execute()
                app.logger.info(f"✅ Deleted album record '{album_name}'")
            except Exception as e:
                app.logger.warning(f"❌ Failed to delete album record for {album_name}: {e}")

        else:
            # === fallback: 本地 SQLite 模式 ===
            photos = Photo.query.filter_by(album=album_name).all()
            for p in photos:
                db.session.delete(p)
            deleted_photos = len(photos)
            db.session.commit()

            album_obj = Album.query.filter_by(name=album_name).first()
            if album_obj:
                db.session.delete(album_obj)
                db.session.commit()
            app.logger.info(f"✅ Local album '{album_name}' deleted ({deleted_photos} photos)")

        flash(f"✅ Album '{album_name}' deleted ({deleted_photos} photos, {deleted_files} files)", "success")
        app.logger.info(f"Album '{album_name}' fully deleted.")
        return redirect(url_for("albums"))

    except Exception as e:
        app.logger.exception(f"delete_album failed: {e}")
        flash(f"❌ Failed to delete album '{album_name}': {e}", "danger")
        return redirect(url_for("albums"))

# --------------------------
# Story 列表
# --------------------------
@app.route("/story_list")
def story_list():
    stories = []

    try:
        if use_supabase and supabase:
            response = supabase.table("story").select("*, image(*)").order("created_at", desc=True).execute()
            if response.data:
                for s in response.data:
                    story = type("StoryObj", (), {})()
                    story.id = s.get("id")
                    story.text = s.get("text")
                    # ✅ 直接保留字符串格式
                    story.created_at = s.get("created_at")
                    story.images = []
                    for img in s.get("image", []):
                        img_obj = type("StoryImageObj", (), {})()
                        img_obj.image_url = img.get("image_url")
                        story.images.append(img_obj)
                    stories.append(story)
        else:
            stories = Story.query.order_by(Story.created_at.desc()).all()
    except Exception as e:
        app.logger.warning(f"⚠️ 获取 Story 列表失败: {e}")
        try:
            stories = Story.query.order_by(Story.created_at.desc()).all()
        except Exception as e2:
            app.logger.error(f"⚠️ SQLite Story 查询失败: {e2}")
            stories = []

    # 修复旧 Cloudinary 图片 URL
    for story in stories:
        for img in story.images:
            if not img.image_url or not img.image_url.startswith("https://res.cloudinary.com/dpr0pl2tf/"):
                try:
                    filename = img.image_url.split("/")[-1] if img.image_url else str(uuid.uuid4())
                    public_id = filename.rsplit(".", 1)[0]
                    new_url, _ = cloudinary.utils.cloudinary_url(f"story/{public_id}")
                    img.image_url = new_url
                except Exception as e:
                    print(f"⚠️ 修复旧 Story 图片失败: {img.image_url} -> {e}")

    return render_template("story_list.html", stories=stories, logged_in=session.get("logged_in", False))

# --------------------------
# Story 详情
# --------------------------
from datetime import datetime

@app.route("/story/<int:story_id>")
def story_detail(story_id):
    try:
        if use_supabase and supabase:
            s = supabase.table("story").select("*, image(*)").eq("id", story_id).single().execute()
            if s.data:
                story = type("StoryObj", (), {})()
                story.id = s.data.get("id")
                story.text = s.data.get("text")
                
                # ⚡ 将字符串转为 datetime
                created_at_str = s.data.get("created_at")
                if created_at_str:
                    try:
                        story.created_at = datetime.fromisoformat(created_at_str)
                    except ValueError:
                        # 如果格式不对，就保留原字符串
                        story.created_at = created_at_str
                else:
                    story.created_at = None

                story.images = []
                for img in s.data.get("image", []):
                    img_obj = type("StoryImageObj", (), {})()
                    img_obj.image_url = img.get("image_url")
                    story.images.append(img_obj)
            else:
                return "Story not found", 404
        else:
            story = Story.query.get_or_404(story_id)
    except Exception as e:
        app.logger.warning(f"⚠️ 获取 Story 详情失败: {e}")
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

        if use_supabase and supabase:
            # 上传到 Supabase
            try:
                # 插入 Story
                res = supabase.table("story").insert({"text": story_text.strip()}).execute()
                story_id = res.data[0]["id"]
                uploaded_images = []

                for file in files:
                    if file and file.filename:
                        img_url = upload_to_cloudinary(file)  # ⚡ 抽成函数
                        if img_url:
                            supabase.table("image").insert({
                                "story_id": story_id,
                                "image_url": img_url
                            }).execute()
                            uploaded_images.append(img_url)
                flash("Story uploaded successfully!", "success")
                return redirect(url_for("story_list"))

            except Exception as e:
                app.logger.exception("Supabase upload_story failed, fallback to SQLite: %s", e)
                # fallback SQLite

        # SQLite 回退逻辑
        new_story = Story(text=story_text.strip())
        db.session.add(new_story)
        db.session.flush()
        for file in files:
            if file and file.filename:
                img_url = upload_to_cloudinary(file)
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
    if use_supabase and supabase:
        try:
            s = supabase.table("story").select("*, image(*)").eq("id", story_id).single().execute()
            if not s.data:
                return "Story not found", 404
            story = type("StoryObj", (), {})()
            story.id = s.data.get("id")
            story.text = s.data.get("text")
            story.images = []
            for img in s.data.get("image", []):
                img_obj = type("StoryImageObj", (), {})()
                img_obj.id = img.get("id")
                img_obj.image_url = img.get("image_url")
                story.images.append(img_obj)
        except Exception as e:
            app.logger.exception("Supabase edit_story failed, fallback to SQLite: %s", e)
            story = Story.query.get_or_404(story_id)
    else:
        story = Story.query.get_or_404(story_id)

    if request.method == "POST":
        text = request.form.get("text")
        if not text or text.strip() == "":
            flash("Story content cannot be empty", "error")
            return render_template("edit_story.html", story=story)

        if use_supabase and supabase:
            try:
                supabase.table("story").update({"text": text.strip()}).eq("id", story_id).execute()

                # 删除选中的旧图
                delete_image_ids = request.form.get("delete_images", "")
                if delete_image_ids:
                    for img_id in delete_image_ids.split(","):
                        supabase.table("image").delete().eq("id", int(img_id)).execute()

                # 上传新图
                files = request.files.getlist("story_images")
                for file in files:
                    if file and file.filename:
                        img_url = upload_to_cloudinary(file)
                        if img_url:
                            supabase.table("image").insert({"story_id": story_id, "image_url": img_url}).execute()
                flash("Story updated", "success")
                return redirect(url_for("story_detail", story_id=story_id))
            except Exception as e:
                app.logger.exception("Supabase edit_story failed, fallback to SQLite: %s", e)
                # fallback SQLite

        # SQLite 回退逻辑
        story_obj = Story.query.get_or_404(story_id)
        story_obj.text = text.strip()
        delete_image_ids = request.form.get("delete_images", "")
        if delete_image_ids:
            for img_id in delete_image_ids.split(","):
                img = StoryImage.query.get(int(img_id))
                if img:
                    db.session.delete(img)
        files = request.files.getlist("story_images")
        for file in files:
            if file and file.filename:
                img_url = upload_to_cloudinary(file)
                if img_url:
                    db.session.add(StoryImage(image_url=img_url, story=story_obj))
        db.session.commit()
        flash("Story updated", "success")
        return redirect(url_for("story_detail", story_id=story_id))

    return render_template("edit_story.html", story=story)

# --------------------------
# 删除 Story（仅登录）
# --------------------------
@app.route("/delete_story/<int:story_id>", methods=["POST"])
@login_required
def delete_story(story_id):
    if use_supabase and supabase:
        try:
            # 删除图片
            supabase.table("image").delete().eq("story_id", story_id).execute()
            # 删除 Story
            supabase.table("story").delete().eq("id", story_id).execute()
            flash("Story deleted.", "info")
            return redirect(url_for("story_list"))
        except Exception as e:
            app.logger.exception("Supabase delete_story failed, fallback to SQLite: %s", e)
            # fallback SQLite

    # SQLite 回退
    story = Story.query.get_or_404(story_id)
    db.session.delete(story)
    db.session.commit()
    flash("Story deleted.", "info")
    return redirect(url_for("story_list"))


def get_albums():
    """从 Supabase 或本地数据库中获取已有相册列表"""
    try:
        if use_supabase and supabase:
            response = supabase.table("photo").select("album").execute()
            albums = sorted(list({item["album"] for item in response.data if item.get("album")}))
        else:
            rows = db.session.query(Photo.album).distinct().all()
            albums = sorted([r[0] for r in rows if r[0]])
        return albums
    except Exception as e:
        app.logger.warning(f"⚠️ 获取相册列表失败: {e}")
        return []

# --------------------------
# Upload photo
# --------------------------
@app.route("/upload", methods=["GET", "POST"])
def upload():
    try:
        if request.method == "GET":
            album_names = []
            try:
                if use_supabase and SUPABASE_SERVICE_ROLE_KEY:
                    supabase_admin = create_client(SUPABASE_URL, SUPABASE_SERVICE_ROLE_KEY)
                    res = supabase_admin.table("album").select("name").order("name", desc=False).execute()
                    album_names = [a["name"] for a in (res.data or [])]
                else:
                    rows = db.session.query(Photo.album).distinct().all()
                    album_names = sorted([r[0] for r in rows if r[0]])
            except Exception as e:
                app.logger.warning(f"获取相册名失败: {e}")
                album_names = []

            return render_template("upload.html", album_names=album_names, last_album=session.get("last_album", ""))

        # ---------- POST ----------
        album_name = (request.form.get("album") or request.form.get("new_album") or "").strip()
        if not album_name:
            return jsonify({"success": False, "error": "album name required"}), 400

        drive_folder_id = (request.form.get("drive_folder_id") or "").strip()
        is_private = request.form.get("is_private", "false").lower() == "true"
        files = request.files.getlist("photo") or []
        if not files:
            return jsonify({"success": False, "error": "no files"}), 400

        uploaded_urls = []
        safe_album = album_name.replace(" ", "_")

        if use_supabase and SUPABASE_SERVICE_ROLE_KEY:
            supabase_admin = create_client(SUPABASE_URL, SUPABASE_SERVICE_ROLE_KEY)
            bucket = supabase_admin.storage.from_(SUPABASE_BUCKET)

            # --- 检查 album 是否存在，不存在则创建 ---
            try:
                existing = supabase_admin.table("album").select("*").eq("name", album_name).execute()
                if not existing.data:
                    # 新建时带上 drive_folder_id
                    supabase_admin.table("album").insert({
                        "name": album_name,
                        "drive_folder_id": drive_folder_id if drive_folder_id else None
                    }).execute()
                else:
                    # 如果已有记录但用户输入了新的 drive_folder_id，则更新
                    if drive_folder_id:
                        supabase_admin.table("album").update({
                            "drive_folder_id": drive_folder_id
                        }).eq("name", album_name).execute()
            except Exception as e:
                app.logger.warning(f"创建/检查 album 失败: {e}")

            # --- 上传每个文件到 Supabase Storage ---
            for f in files:
                if not f or not f.filename:
                    continue

                filename = f"{uuid.uuid4().hex}_{secure_filename(f.filename)}"
                path = f"{safe_album}/{filename}"

                try:
                    file_bytes = f.read()
                    bucket.upload(
                        path,
                        file_bytes,
                        file_options={"content-type": f.mimetype or "application/octet-stream", "upsert": "true"}
                    )
                    public_url = f"{SUPABASE_URL.rstrip('/')}/storage/v1/object/public/{SUPABASE_BUCKET}/{quote(path, safe='')}"

                    supabase_admin.table("photo").insert({
                        "album": album_name,
                        "url": public_url,
                        "is_private": is_private
                    }).execute()

                    uploaded_urls.append(public_url)

                except Exception as e:
                    app.logger.exception(f"Supabase 上传失败，尝试本地保存: {e}")
                    local_dir = os.path.join("static", "uploads", safe_album)
                    os.makedirs(local_dir, exist_ok=True)
                    local_path = os.path.join(local_dir, filename)
                    with open(local_path, "wb") as out:
                        out.write(file_bytes)
                    local_url = url_for("static", filename=f"uploads/{safe_album}/{filename}", _external=True)
                    uploaded_urls.append(local_url)

        else:
            # --- 本地保存模式 ---
            os.makedirs(os.path.join("static", "uploads", safe_album), exist_ok=True)
            for f in files:
                if not f or not f.filename:
                    continue
                filename = f"{uuid.uuid4().hex}_{secure_filename(f.filename)}"
                local_path = os.path.join("static", "uploads", safe_album, filename)
                f.save(local_path)
                public_url = url_for("static", filename=f"uploads/{safe_album}/{filename}", _external=True)

                try:
                    new_photo = Photo(album=album_name, url=public_url, is_private=is_private)
                    db.session.add(new_photo)
                except Exception as e:
                    app.logger.warning(f"写本地 DB 失败: {e}")

            try:
                db.session.commit()
            except Exception:
                db.session.rollback()

        session["last_album"] = album_name
        return jsonify({"success": True, "uploads": uploaded_urls})

    except Exception as e:
        app.logger.exception("Upload failed")
        return jsonify({"success": False, "error": str(e)}), 500

# --------------------------
# Upload private (logged-in required)
# --------------------------
@app.route("/upload_private", methods=["POST"])
def upload_private():
    if not session.get("logged_in"):
        return jsonify({"success": False, "error": "login required"}), 401

    try:
        album = (request.form.get("album") or request.form.get("new_album") or "").strip()
        if not album:
            return jsonify({"success": False, "error": "album name required"}), 400

        files = request.files.getlist("photo")
        if not files:
            return jsonify({"success": False, "error": "no files"}), 400

        uploaded_urls = []
        for f in files:
            if not f or not f.filename:
                continue
            raw = f.read()
            buf = compress_image_bytes(raw)   # BytesIO
            file_bytes = buf.getvalue()       # ✅ 转成 bytes
            filename = safe_filename(f.filename)

            public_url = None
            if use_supabase and supabase:
                try:
                    path = f"private/{album}/{filename}"
                    res = supabase.storage.from_(SUPABASE_BUCKET).upload(path, file_bytes, {"upsert": True})
                    pub = supabase.storage.from_(SUPABASE_BUCKET).get_public_url(path)
                    if isinstance(pub, dict):
                        public_url = pub.get("publicURL") or pub.get("public_url") or pub.get("publicUrl")
                    elif isinstance(pub, str):
                        public_url = pub
                except Exception as e:
                    app.logger.exception("Supabase private upload failed, fallback to local: %s", e)
                    public_url = None

            if not public_url:
                local_path = os.path.join(LOCAL_UPLOAD_DIR, filename)
                with open(local_path, "wb") as out:
                    out.write(file_bytes)
                public_url = url_for('static', filename=f"uploads/{filename}", _external=True)

            new_photo = Photo(album=album, url=public_url, is_private=True)
            db.session.add(new_photo)
            db.session.commit()
            uploaded_urls.append(public_url)

        return jsonify({"success": True, "urls": uploaded_urls, "album": album})

    except Exception as e:
        app.logger.exception("upload_private failed")
        return jsonify({"success": False, "error": str(e)}), 500
        
# --------------------------
# Login / logout
# --------------------------
@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        username = request.form.get("username", "")
        password = request.form.get("password", "")
        # keep your original credential check
        if username == "xia0720" and password == "qq123456":
            session["logged_in"] = True
            flash("Logged in.", "success")
            next_url = request.args.get("next")
            return redirect(next_url or url_for("story_list"))
        else:
            flash("Invalid credentials.", "danger")
            return redirect(url_for("login"))
    return render_template("login.html")


@app.route("/logout")
def logout():
    session.pop("logged_in", None)
    flash("Logged out.", "info")
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
# Private-space index (shows private albums)
# --------------------------
@app.route("/private_space")
def private_space():
    # protect page - only allow logged_in; but if you want guests to see list, remove guard
    if not session.get("logged_in"):
        return redirect(url_for("login", next=request.path))

    try:
        rows = db.session.query(Photo.album, Photo.url).filter_by(is_private=True).order_by(Photo.album, Photo.created_at).all()
        album_map = {}
        for album, url in rows:
            if album not in album_map:
                album_map[album] = url
        album_names = sorted(album_map.keys())
        album_covers = {k: v for k, v in album_map.items()}
        return render_template("private_album.html", album_names=album_names, album_covers=album_covers, last_album=session.get("last_private_album", ""))
    except Exception as e:
        app.logger.exception("private_space failed")
        return f"Error loading private space: {e}", 500


# --------------------------
# View private album (only logged in)
# --------------------------
@app.route("/private_space/<album_name>")
def view_private_album(album_name):
    if not session.get("logged_in"):
        return redirect(url_for("login", next=request.path))
    try:
        photos = Photo.query.filter_by(album=album_name, is_private=True).order_by(Photo.created_at.desc()).all()
        images = []
        for p in photos:
            images.append({
                "id": p.id,
                "url": p.url,
                "secure_url": p.url,   # for compatibility with templates expecting secure_url
                "public_id": str(p.id)
            })
        return render_template("view_private_album.html", album_name=album_name, images=images)
    except Exception as e:
        app.logger.exception("view_private_album failed")
        return f"Error loading private album: {e}", 500


# --------------------------
# Save photo endpoint (compatibility - JSON)
# --------------------------
@app.route("/save_photo", methods=["POST"])
def save_photo():
    try:
        data = None
        if request.is_json:
            data = request.get_json()
        else:
            # try form
            data = request.form.to_dict()

        album = data.get("album") or data.get("album_name")
        url = data.get("url") or data.get("file_url")
        is_private = data.get("private") in (True, "true", "1", "on")

        if not album or not url:
            return jsonify({"success": False, "error": "missing album or url"}), 400

        # prevent duplicates
        exists = Photo.query.filter_by(url=url).first()
        if exists:
            return jsonify({"success": True, "message": "already_exists"})

        new_photo = Photo(album=album, url=url, is_private=is_private)
        db.session.add(new_photo)
        db.session.commit()
        return jsonify({"success": True})
    except Exception as e:
        app.logger.exception("save_photo failed")
        return jsonify({"success": False, "error": str(e)}), 500
# --------------------------
# 启动
# --------------------------
if __name__ == "__main__":
    app.run(debug=True)

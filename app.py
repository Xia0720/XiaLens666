import os
from flask import (Flask, render_template, request, redirect, url_for, session, flash)
from flask_sqlalchemy import SQLAlchemy
from flask_migrate import Migrate
from werkzeug.security import generate_password_hash, check_password_hash
import cloudinary
import cloudinary.uploader
import cloudinary.api
from functools import wraps

app = Flask(__name__)
app.secret_key = os.getenv("FLASK_SECRET", "xia0720_secret")

# 数据库配置
database_url = os.getenv("DATABASE_URL")
if database_url:
    app.config['SQLALCHEMY_DATABASE_URI'] = database_url
else:
    app.config['SQLALCHEMY_DATABASE_URI'] = "sqlite:///app.db"
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False

db = SQLAlchemy(app)
migrate = Migrate(app, db)

# Cloudinary 配置
cloudinary.config(
    cloud_name='dpr0pl2tf',
    api_key='548549517251566',
    api_secret='9o-PlPBRQzQPfuVCQfaGrUV3_IE'
)

# ----- 数据模型 -----
class Album(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(255), unique=True, nullable=False)
    password_hash = db.Column(db.String(255), nullable=True)

    def set_password(self, password):
        self.password_hash = generate_password_hash(password)

    def check_password(self, password):
        if not self.password_hash:
            return True  # 无密码即公开访问
        return check_password_hash(self.password_hash, password)


# ----- 管理员登录保护装饰器 -----
def login_required(f):
    @wraps(f)
    def wrapper(*args, **kwargs):
        if not session.get("logged_in"):
            return redirect(url_for("login", next=request.path))
        return f(*args, **kwargs)
    return wrapper


# ----- 全局模板变量 -----
@app.context_processor
def inject_logged_in():
    return dict(logged_in=session.get("logged_in", False))


# ----- 路由 -----

@app.route("/")
def index():
    return render_template("index.html")


@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        username = request.form.get("username")
        password = request.form.get("password")
        # 简单示例，建议换成更安全的验证方式
        if username == "xia0720" and password == "qq123456":
            session["logged_in"] = True
            flash("登录成功", "success")
            next_page = request.args.get("next")
            return redirect(next_page or url_for("index"))
        else:
            flash("用户名或密码错误", "danger")
    return render_template("login.html")


@app.route("/logout")
def logout():
    session.pop("logged_in", None)
    flash("已登出", "info")
    return redirect(url_for("index"))


@app.route("/albums")
def albums():
    albums = Album.query.all()
    album_data = []
    for album in albums:
        try:
            res = cloudinary.api.resources(type="upload", prefix=album.name, max_results=1)
            cover_url = res['resources'][0]['secure_url'] if res['resources'] else None
        except Exception:
            cover_url = None
        album_data.append({
            "name": album.name,
            "cover": cover_url,
            "has_password": bool(album.password_hash)
        })
    return render_template("albums.html", albums=album_data)


@app.route("/album/<album_name>", methods=["GET", "POST"])
def view_album(album_name):
    album = Album.query.filter_by(name=album_name).first()
    if not album:
        # 数据库里无此相册则自动创建一个无密码相册
        album = Album(name=album_name)
        db.session.add(album)
        db.session.commit()

    # 需要密码且未授权访问
    if album.password_hash and not session.get(f"album_access_{album_name}"):
        if request.method == "POST":
            password = request.form.get("password")
            if album.check_password(password):
                session[f"album_access_{album_name}"] = True
                flash("密码验证通过！", "success")
                return redirect(url_for("view_album", album_name=album_name))
            else:
                flash("密码错误！", "danger")
        return render_template("album_password.html", album_name=album_name)

    # 已授权或无密码，显示图片
    try:
        res = cloudinary.api.resources(type="upload", prefix=album_name, max_results=100)
        image_urls = [r['secure_url'] for r in res['resources']]
    except Exception as e:
        flash(f"获取相册图片失败: {str(e)}", "danger")
        image_urls = []

    return render_template("view_album.html", album_name=album_name, image_urls=image_urls)


@app.route("/album/<album_name>/set_password", methods=["GET", "POST"])
@login_required
def set_album_password(album_name):
    album = Album.query.filter_by(name=album_name).first()
    if not album:
        album = Album(name=album_name)
        db.session.add(album)

    if request.method == "POST":
        password = request.form.get("password")
        if password:
            album.set_password(password)
        else:
            album.password_hash = None
        db.session.commit()
        flash(f"相册 '{album_name}' 的密码已更新", "success")
        return redirect(url_for("albums"))

    return render_template("set_album_password.html", album=album)


@app.route("/upload", methods=["GET", "POST"])
@login_required
def upload():
    if request.method == "POST":
        folder = request.form.get("folder")
        photos = request.files.getlist("photo")
        if not folder:
            flash("请输入相册名称", "warning")
            return redirect(request.url)
        if not photos or all(photo.filename == '' for photo in photos):
            flash("请选择上传的图片", "warning")
            return redirect(request.url)

        for photo in photos:
            if photo and photo.filename != '':
                try:
                    cloudinary.uploader.upload(photo, folder=folder)
                except Exception as e:
                    flash(f"上传失败: {str(e)}", "danger")
                    return redirect(request.url)
        flash(f"成功上传到相册: {folder}", "success")
        return redirect(url_for("upload"))
    return render_template("upload.html")


@app.route("/delete_images", methods=["POST"])
@login_required
def delete_images():
    public_ids = request.form.getlist("public_ids")
    album_name = request.form.get("album_name")
    if not public_ids:
        flash("未选择任何图片进行删除", "warning")
        return redirect(url_for("view_album", album_name=album_name))
    try:
        cloudinary.api.delete_resources(public_ids)
        flash(f"成功删除 {len(public_ids)} 张图片", "success")
    except Exception as e:
        flash(f"删除失败: {str(e)}", "danger")
    return redirect(url_for("view_album", album_name=album_name))


if __name__ == "__main__":
    app.run(debug=True)


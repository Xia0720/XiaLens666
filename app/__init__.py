import os
from flask import Flask
from flask_sqlalchemy import SQLAlchemy
from flask_migrate import Migrate
import cloudinary

app = Flask(__name__)
app.secret_key = os.getenv("FLASK_SECRET", "xia0720_secret")

# Cloudinary 配置
cloudinary.config(
    cloud_name='dpr0pl2tf',
    api_key='548549517251566',
    api_secret='9o-PlPBRQzQPfuVCQfaGrUV3_IE'
)

# 数据库配置
database_url = os.getenv("DATABASE_URL") or "sqlite:///stories.db"
app.config['SQLALCHEMY_DATABASE_URI'] = database_url
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False

# 初始化数据库
db = SQLAlchemy(app)
migrate = Migrate(app, db)

# ✅ 导入模型和路由
from app import models
from app import routes
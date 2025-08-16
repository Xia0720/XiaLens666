from app import db
from werkzeug.security import generate_password_hash, check_password_hash
from datetime import datetime

class Album(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(255), unique=True, nullable=False)
    password_hash = db.Column(db.String(255), nullable=True)

    def set_password(self, password):
        self.password_hash = generate_password_hash(password)

    def check_password(self, password):
        if not self.password_hash:
            return True  # 无密码视为公开
        return check_password_hash(self.password_hash, password)


class Story(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    text = db.Column(db.Text, nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    images = db.relationship("Image", backref="story", cascade="all, delete-orphan")


class Image(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    image_url = db.Column(db.String(255), nullable=False)
    story_id = db.Column(db.Integer, db.ForeignKey("story.id"), nullable=False)

from flask_sqlalchemy import SQLAlchemy

db = SQLAlchemy()

class Album(db.Model):
    __tablename__ = 'album'
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(100), unique=True, nullable=False)
    # 其他字段

class AlbumCover(db.Model):
    __tablename__ = 'album_cover'
    id = db.Column(db.Integer, primary_key=True)
    album_id = db.Column(db.Integer, db.ForeignKey('album.id'))
    cover_public_id = db.Column(db.String(255))  # ✅ 缺这个

class Photo(db.Model):
    __tablename__ = 'photo'
    id = db.Column(db.Integer, primary_key=True)
    album_id = db.Column(db.Integer, db.ForeignKey('album.id'))
    url = db.Column(db.String(500))  # ✅ 缺这个


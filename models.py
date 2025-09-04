from flask_sqlalchemy import SQLAlchemy
from datetime import datetime

db = SQLAlchemy()

class Album(db.Model):
    __tablename__ = "album"
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(255), nullable=False, unique=True)

class AlbumCover(db.Model):
    __tablename__ = "album_covers"
    id = db.Column(db.Integer, primary_key=True)
    album_id = db.Column(db.Integer, db.ForeignKey("album.id"), nullable=False, unique=True)
    cover_public_id = db.Column(db.String(255), nullable=False)
    album = db.relationship("Album", backref=db.backref("cover", uselist=False))

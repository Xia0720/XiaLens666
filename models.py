from extensions import db

class Album(db.Model):
    __tablename__ = 'album'
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(100), unique=True, nullable=False)
    cover = db.Column(db.String(500))  # ✅ 新增字段，存封面 URL

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


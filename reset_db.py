from main import app, db, Album, AlbumCover, Photo, sync_all_to_db

def main():
    with app.app_context():
        db.drop_all()
        db.create_all()

        result = sync_all_to_db()
        print(result)

        # 打印表结构
        for model in [Album, AlbumCover, Photo]:
            print(f"\n=== {model.__tablename__} ===")
            for col in model.__table__.columns:
                print(f"{col.name} - {col.type}")

if __name__ == "__main__":
    main()






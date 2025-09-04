from main import app, db, sync_all_to_db

def main():
    with app.app_context():
        db.drop_all()
        db.create_all()
        result = sync_all_to_db()
        print(result)

if __name__ == '__main__':
    main()



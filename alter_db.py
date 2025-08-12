from app import app, db
from sqlalchemy import text

with app.app_context():
    db.session.execute(
        text("ALTER TABLE story ADD COLUMN IF NOT EXISTS created_at TIMESTAMP DEFAULT NOW()")
    )
    db.session.commit()
    print("✅ created_at 字段已确认存在（新加或原本就有）")

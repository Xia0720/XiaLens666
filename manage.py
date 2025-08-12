from app import app, db
from flask_migrate import Migrate
from flask.cli import FlaskGroup

migrate = Migrate(app, db)
cli = FlaskGroup(app)

if __name__ == "__main__":
    cli()

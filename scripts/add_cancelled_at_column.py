"""One-off hotfix migration: add sales.cancelled_at for the sale-cancel guard.

DELETE /api/sales/<id> no longer hard-deletes financial records; it stamps
this column instead. Freshly created databases get the column from the model
via db.create_all(), so this script exists ONLY for databases that already
hold data.

Usage (run once, from the project root, with the same DATABASE_URL the app
uses — omit DATABASE_URL for the default local SQLite file):

    python scripts/add_cancelled_at_column.py

The script is idempotent: if the column already exists it reports that and
exits 0 without touching anything.
"""
import os
import sys

from sqlalchemy import create_engine, inspect, text


def resolve_database_url() -> str:
    """Mirror app.py's URL resolution so the script migrates the same DB."""
    url = os.environ.get('DATABASE_URL')
    if url:
        if url.startswith('postgres://'):
            url = url.replace('postgres://', 'postgresql://', 1)
        return url
    # Flask-SQLAlchemy 3.x resolves the relative 'sqlite:///pos.db' used by
    # app.py against the Flask instance folder.
    project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    return 'sqlite:///' + os.path.join(project_root, 'instance', 'pos.db').replace('\\', '/')


def main() -> int:
    url = resolve_database_url()
    engine = create_engine(url)

    inspector = inspect(engine)
    if 'sales' not in inspector.get_table_names():
        print(f"ERROR: no 'sales' table found at {engine.url!r}. "
              "Run the app once to create the schema, or check DATABASE_URL.")
        return 1

    columns = [c['name'] for c in inspector.get_columns('sales')]
    if 'cancelled_at' in columns:
        print('sales.cancelled_at already exists — nothing to do.')
        return 0

    with engine.begin() as conn:
        # TIMESTAMP works for both SQLite and PostgreSQL; NULL means
        # 'not cancelled', so no backfill is needed.
        conn.execute(text('ALTER TABLE sales ADD COLUMN cancelled_at TIMESTAMP NULL'))
    print(f'Added sales.cancelled_at to {engine.url!r}.')
    return 0


if __name__ == '__main__':
    sys.exit(main())

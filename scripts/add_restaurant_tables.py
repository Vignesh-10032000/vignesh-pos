import os
import sys

from sqlalchemy import create_engine, inspect, text
from sqlalchemy.exc import OperationalError

def resolve_database_url() -> str:
    url = os.environ.get('DATABASE_URL')
    if url:
        if url.startswith('postgres://'):
            url = url.replace('postgres://', 'postgresql://', 1)
        return url
    project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    return 'sqlite:///' + os.path.join(project_root, 'instance', 'pos.db').replace('\\', '/')

def main() -> int:
    url = resolve_database_url()
    engine = create_engine(url)

    inspector = inspect(engine)
    
    # 1. Add new columns
    with engine.begin() as conn:
        # Check and add to products
        if 'products' in inspector.get_table_names():
            columns = [c['name'] for c in inspector.get_columns('products')]
            if 'is_veg' not in columns:
                conn.execute(text("ALTER TABLE products ADD COLUMN is_veg BOOLEAN DEFAULT 1"))
                print("Added products.is_veg")

        # Check and add to sales
        if 'sales' in inspector.get_table_names():
            columns = [c['name'] for c in inspector.get_columns('sales')]
            if 'order_type' not in columns:
                conn.execute(text("ALTER TABLE sales ADD COLUMN order_type VARCHAR(20) DEFAULT 'COUNTER'"))
            if 'table_id' not in columns:
                conn.execute(text("ALTER TABLE sales ADD COLUMN table_id INTEGER"))
            if 'status' not in columns:
                conn.execute(text("ALTER TABLE sales ADD COLUMN status VARCHAR(20) DEFAULT 'COMPLETED'"))
            if 'opened_at' not in columns:
                conn.execute(text("ALTER TABLE sales ADD COLUMN opened_at TIMESTAMP"))
            if 'waiter_name' not in columns:
                conn.execute(text("ALTER TABLE sales ADD COLUMN waiter_name VARCHAR(100)"))
            print("Added sales columns")

        # Check and add to sale_items
        if 'sale_items' in inspector.get_table_names():
            columns = [c['name'] for c in inspector.get_columns('sale_items')]
            if 'cooking_notes' not in columns:
                conn.execute(text("ALTER TABLE sale_items ADD COLUMN cooking_notes TEXT"))
                print("Added sale_items.cooking_notes")

    # 2. Use app context to create new tables and seed data
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    try:
        from app import app
        from extensions import db
        from models import DiningTable
    except ImportError as e:
        print(f"Could not import app: {e}")
        return 1

    with app.app_context():
        # Create missing tables (dining_tables, kots, kot_items)
        db.create_all()
        print("Created new tables.")

        # Seed 8 demo tables if none exist
        if DiningTable.query.count() == 0:
            tables = [
                DiningTable(table_number="T1", area="Main Hall", capacity=2),
                DiningTable(table_number="T2", area="Main Hall", capacity=4),
                DiningTable(table_number="T3", area="Main Hall", capacity=4),
                DiningTable(table_number="T4", area="Main Hall", capacity=6),
                DiningTable(table_number="T5", area="AC Dining", capacity=2),
                DiningTable(table_number="T6", area="AC Dining", capacity=4),
                DiningTable(table_number="T7", area="AC Dining", capacity=4),
                DiningTable(table_number="T8", area="AC Dining", capacity=8),
            ]
            db.session.bulk_save_objects(tables)
            db.session.commit()
            print("Seeded 8 demo dining tables.")
        else:
            print("Dining tables already seeded.")

    return 0

if __name__ == '__main__':
    sys.exit(main())

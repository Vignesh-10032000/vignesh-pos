from app import create_app

if __name__ == '__main__':
    print("Initializing SQLite database and seeding demo data...")
    app = create_app()
    print("[OK] Database initialized successfully with categories, products, and sample sales!")

"""Concurrency and Race-Condition Stress Suite for VGL POS (Version A).

Tests multi-threaded operations against SQLite in WAL mode:
1. Concurrent item additions on open orders without item loss or database lock errors.
2. Race conditions on settlement to guarantee zero double-settlements and zero double-deductions.
3. Concurrent KOT dispatches across distinct tables ensuring monotonically unique sequence numbers.
4. Duplicate KOT prevention on the same table under simultaneous dispatch requests.
5. Concurrent table opening race prevention.
"""
import os
import sys
import tempfile
import unittest

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from concurrent.futures import ThreadPoolExecutor, as_completed
from app import create_app
from extensions import db
from models import DiningTable, Category, Product, Sale, SaleItem, KOT


class ConcurrencyTestCase(unittest.TestCase):
    """Stress tests concurrency and thread safety under realistic multi-user load."""

    def setUp(self):
        # Create a unique temporary database file for thread-shared SQLite storage
        self.temp_dir = tempfile.gettempdir()
        self.db_filename = f"test_concurrency_{os.getpid()}_{id(self)}.db"
        self.db_path = os.path.join(self.temp_dir, self.db_filename)
        if os.path.exists(self.db_path):
            os.remove(self.db_path)

        self.app = create_app({
            'TESTING': True,
            'SQLALCHEMY_DATABASE_URI': f'sqlite:///{self.db_path}',
            'SECRET_KEY': 'concurrency_test_secret_key',
            'RATE_LIMIT_ENABLED': 'false'
        })

        with self.app.app_context():
            db.create_all()
            self._seed_data()

    def tearDown(self):
        with self.app.app_context():
            db.session.remove()
            db.engine.dispose()
        # Clean up database files (including WAL / SHM files if present)
        for ext in ['', '-wal', '-shm']:
            p = self.db_path + ext
            if os.path.exists(p):
                try:
                    os.remove(p)
                except OSError:
                    pass

    def _seed_data(self):
        cat = Category(name="Restaurant F&B", icon="🍽️")
        db.session.add(cat)
        db.session.commit()

        self.p1 = Product(name="Masala Dosa", sku="MD01", price=60.0, stock=200,
                          category_id=cat.id, gst_rate=5, is_veg=True)
        self.p2 = Product(name="Filter Coffee", sku="FC01", price=25.0, stock=200,
                          category_id=cat.id, gst_rate=5, is_veg=True)
        self.p3 = Product(name="Idli Vada Combo", sku="IV01", price=45.0, stock=200,
                          category_id=cat.id, gst_rate=5, is_veg=True)

        self.t1 = DiningTable(table_number="T1", area="Main Hall", capacity=4, status="AVAILABLE")
        self.t2 = DiningTable(table_number="T2", area="Main Hall", capacity=4, status="AVAILABLE")
        self.t3 = DiningTable(table_number="T3", area="AC Dining", capacity=6, status="AVAILABLE")

        db.session.add_all([self.p1, self.p2, self.p3, self.t1, self.t2, self.t3])
        db.session.commit()

    def test_concurrent_add_items_no_loss(self):
        """Verify 10 simultaneous item additions across multiple threads are preserved."""
        client = self.app.test_client()
        # Open Table 1
        open_res = client.post('/api/tables/1/open', json={'waiter_name': 'Ramesh'})
        self.assertEqual(open_res.status_code, 200)

        num_threads = 10
        item_qty = 2  # Each request adds 2 quantities

        def worker_add_item(worker_id):
            c = self.app.test_client()
            resp = c.post('/api/tables/1/items', json={
                'items': [{
                    'product_id': 1,
                    'quantity': item_qty,
                    'cooking_notes': f'Worker {worker_id}'
                }]
            })
            return resp.status_code, resp.get_json()

        with ThreadPoolExecutor(max_workers=5) as executor:
            futures = [executor.submit(worker_add_item, i) for i in range(num_threads)]
            results = [f.result() for f in as_completed(futures)]

        # All requests should return 200 OK
        status_codes = [r[0] for r in results]
        self.assertEqual(status_codes, [200] * num_threads, f"Some requests failed: {status_codes}")

        # Verify database state
        with self.app.app_context():
            table = DiningTable.query.get(1)
            order = Sale.query.get(table.current_order_id)
            total_quantity = sum(item.quantity for item in order.items)
            self.assertEqual(total_quantity, num_threads * item_qty)
            self.assertEqual(len(order.items), num_threads)

    def test_concurrent_settlement_race_condition(self):
        """Verify simultaneous settlements on the same table allow exactly one success."""
        client = self.app.test_client()
        client.post('/api/tables/1/open', json={'waiter_name': 'Suresh'})
        # Add 3 quantities of Masala Dosa (price 60.0 each, total = 180.0)
        client.post('/api/tables/1/items', json={'items': [{'product_id': 1, 'quantity': 3}]})

        def worker_settle(worker_id):
            c = self.app.test_client()
            resp = c.post('/api/tables/1/settle', json={
                'amount_paid': 200.0,
                'payment_method': 'cash'
            })
            return resp.status_code, resp.get_json()

        num_workers = 3
        with ThreadPoolExecutor(max_workers=num_workers) as executor:
            futures = [executor.submit(worker_settle, i) for i in range(num_workers)]
            results = [f.result() for f in as_completed(futures)]

        status_codes = [r[0] for r in results]
        success_count = status_codes.count(200)
        rejected_count = status_codes.count(400)

        self.assertEqual(success_count, 1, f"Expected exactly 1 settlement to succeed, got {success_count} ({status_codes})")
        self.assertEqual(rejected_count, num_workers - 1, f"Expected {num_workers - 1} rejections, got {rejected_count}")

        # Stock deduction verification: stock must drop from 200 to 197 (NOT 194 or 191)
        with self.app.app_context():
            p = Product.query.get(1)
            self.assertEqual(p.stock, 197, f"Product stock was deducted incorrectly: {p.stock}")
            table = DiningTable.query.get(1)
            self.assertEqual(table.status, 'AVAILABLE')
            self.assertIsNone(table.current_order_id)

    def test_concurrent_kot_firing_distinct_tables(self):
        """Verify concurrent KOT generation across multiple tables yields distinct sequential numbers."""
        client = self.app.test_client()
        for tid in [1, 2, 3]:
            client.post(f'/api/tables/{tid}/open', json={'waiter_name': f'Captain_{tid}'})
            client.post(f'/api/tables/{tid}/items', json={'items': [{'product_id': 1, 'quantity': 1}]})

        def worker_fire_kot(tid):
            c = self.app.test_client()
            resp = c.post(f'/api/tables/{tid}/kot')
            return resp.status_code, resp.get_json()

        with ThreadPoolExecutor(max_workers=3) as executor:
            futures = [executor.submit(worker_fire_kot, tid) for tid in [1, 2, 3]]
            results = [f.result() for f in as_completed(futures)]

        status_codes = [r[0] for r in results]
        self.assertEqual(status_codes, [200, 200, 200])

        kot_numbers = sorted([r[1]['kot']['kot_number'] for r in results])
        self.assertEqual(kot_numbers, [1, 2, 3], f"KOT numbers not sequential: {kot_numbers}")

        with self.app.app_context():
            all_kots = KOT.query.all()
            self.assertEqual(len(all_kots), 3)

    def test_concurrent_kot_firing_same_table_no_duplicate(self):
        """Verify two concurrent KOT dispatches on the same table do not generate duplicate tickets."""
        client = self.app.test_client()
        client.post('/api/tables/1/open', json={'waiter_name': 'Captain'})
        client.post('/api/tables/1/items', json={'items': [{'product_id': 1, 'quantity': 1}]})

        def worker_fire_same(worker_id):
            c = self.app.test_client()
            resp = c.post('/api/tables/1/kot')
            return resp.status_code, resp.get_json()

        with ThreadPoolExecutor(max_workers=2) as executor:
            f1 = executor.submit(worker_fire_same, 1)
            f2 = executor.submit(worker_fire_same, 2)
            r1, r2 = f1.result(), f2.result()

        codes = sorted([r1[0], r2[0]])
        self.assertEqual(codes, [200, 400], f"Expected one 200 and one 400, got: {codes}")

        with self.app.app_context():
            table_kots = KOT.query.filter_by(table_id=1).all()
            self.assertEqual(len(table_kots), 1, "Duplicate KOT records were created in the database")

    def test_concurrent_open_table_race_condition(self):
        """Verify two concurrent open requests on an AVAILABLE table allow only one to succeed."""
        def worker_open(worker_id):
            c = self.app.test_client()
            resp = c.post('/api/tables/1/open', json={'waiter_name': f'Waiter_{worker_id}'})
            return resp.status_code, resp.get_json()

        with ThreadPoolExecutor(max_workers=2) as executor:
            f1 = executor.submit(worker_open, 1)
            f2 = executor.submit(worker_open, 2)
            r1, r2 = f1.result(), f2.result()

        codes = sorted([r1[0], r2[0]])
        self.assertEqual(codes, [200, 400], f"Expected one 200 and one 400, got: {codes}")


if __name__ == '__main__':
    unittest.main()

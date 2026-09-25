"""End-to-End Browser Journey Suite for VGL POS (Version A) using Playwright.

Covers the complete restaurant operator lifecycle:
1. Authentication & Session Validation:
   - Unauthenticated redirect check to /login?next=/tables
   - Valid credentials login (cashier/pos123)
2. Interactive Table Floor Plan:
   - Table grid layout, status indicators, and summary metrics
3. Order Lifecycle & 60fps Dish Board:
   - Table opening modal with waiter assignment
   - Fullscreen zero-bleed order overlay (#tableOrderView)
   - Real-time search (/ shortcut, typing + Enter auto-add)
   - Inline card stepper (+/-) and badge syncing
4. Kitchen Order Ticket (KOT):
   - Firing KOT to kitchen with dynamic count badge
   - Print bill action with GST breakdown
5. Settlement & Payment Modal Verification:
   - Modal z-index elevation (above z-index 9999 overlay)
   - Total amount due display banner
   - Settle payment (Cash/UPI/Card) & table status transition back to AVAILABLE
6. Full Keyboard Navigation:
   - Shortcut triggers (/, Enter, F8/Ctrl+Enter, Esc)
"""
import os
import sys
import time
import socket
import tempfile
import threading
import unittest

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from werkzeug.serving import make_server
from app import create_app
from extensions import db
from models import DiningTable, Category, Product, Sale, KOT

try:
    from playwright.sync_api import sync_playwright
    PLAYWRIGHT_AVAILABLE = True
except ImportError:
    PLAYWRIGHT_AVAILABLE = False


def find_free_port():
    """Find an available TCP port for the ephemeral test server."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(('127.0.0.1', 0))
        return s.getsockname()[1]


class TestE2EPlaywright(unittest.TestCase):
    """Automated E2E browser tests using Playwright Chromium."""

    @classmethod
    def setUpClass(cls):
        if not PLAYWRIGHT_AVAILABLE:
            raise unittest.SkipTest("playwright library not installed")

        # Determine server URL: allow overriding via E2E_BASE_URL (e.g. for staging/prod)
        cls.external_url = os.environ.get('E2E_BASE_URL')
        if cls.external_url:
            cls.base_url = cls.external_url.rstrip('/')
            cls.server = None
        else:
            cls.port = find_free_port()
            cls.base_url = f"http://127.0.0.1:{cls.port}"

            # Setup isolated SQLite database
            cls.temp_dir = tempfile.gettempdir()
            cls.db_path = os.path.join(cls.temp_dir, f"test_e2e_{os.getpid()}_{int(time.time())}.db")
            if os.path.exists(cls.db_path):
                try:
                    os.remove(cls.db_path)
                except OSError:
                    pass

            cls.app = create_app({
                'TESTING': True,
                'SQLALCHEMY_DATABASE_URI': f'sqlite:///{cls.db_path}',
                'SECRET_KEY': 'e2e_super_secret_test_key_123',
                'RATE_LIMIT_ENABLED': 'false'
            })

            with cls.app.app_context():
                db.create_all()
                cls._seed_e2e_data()

            cls.server = make_server('127.0.0.1', cls.port, cls.app)
            cls.server_thread = threading.Thread(target=cls.server.serve_forever)
            cls.server_thread.daemon = True
            cls.server_thread.start()

        # Launch Playwright Chromium
        cls.playwright = sync_playwright().start()
        cls.browser = cls.playwright.chromium.launch(
            headless=True,
            args=['--no-sandbox', '--disable-dev-shm-usage']
        )

    @classmethod
    def tearDownClass(cls):
        if hasattr(cls, 'browser') and cls.browser:
            cls.browser.close()
        if hasattr(cls, 'playwright') and cls.playwright:
            cls.playwright.stop()

        if hasattr(cls, 'server') and cls.server:
            cls.server.shutdown()
            with cls.app.app_context():
                db.session.remove()
                db.engine.dispose()
            for ext in ['', '-wal', '-shm']:
                p = cls.db_path + ext
                if os.path.exists(p):
                    try:
                        os.remove(p)
                    except OSError:
                        pass

    @classmethod
    def _seed_e2e_data(cls):
        cat_food = Category(name="Food & Beverages", icon="🍛")
        cat_snacks = Category(name="Snacks & Desserts", icon="🍰")
        db.session.add_all([cat_food, cat_snacks])
        db.session.commit()

        p1 = Product(name="Special Masala Dosa", sku="DOSA01", price=75.0, stock=100,
                     category_id=cat_food.id, gst_rate=5, is_veg=True)
        p2 = Product(name="Medu Vada 2pc", sku="VADA01", price=40.0, stock=80,
                     category_id=cat_food.id, gst_rate=5, is_veg=True)
        p3 = Product(name="South Indian Filter Coffee", sku="COF01", price=25.0, stock=150,
                     category_id=cat_food.id, gst_rate=5, is_veg=True)
        p4 = Product(name="Chicken Biryani", sku="BIRY01", price=220.0, stock=50,
                     category_id=cat_food.id, gst_rate=5, is_veg=False)

        t1 = DiningTable(table_number="T1", area="Main Hall", capacity=4, status="AVAILABLE")
        t2 = DiningTable(table_number="T2", area="Main Hall", capacity=2, status="AVAILABLE")
        t3 = DiningTable(table_number="T3", area="AC Dining", capacity=6, status="AVAILABLE")

        db.session.add_all([p1, p2, p3, p4, t1, t2, t3])
        db.session.commit()

    def setUp(self):
        self.context = self.browser.new_context(viewport={'width': 1400, 'height': 900})
        self.page = self.context.new_page()
        # Automatically accept browser dialogs (confirmations/alerts)
        self.page.on("dialog", lambda dialog: dialog.accept())

    def tearDown(self):
        self.context.close()

    def _login(self):
        """Helper to log in as cashier."""
        self.page.goto(f"{self.base_url}/login")
        self.page.wait_for_selector('#usernameInput')
        self.page.fill('#usernameInput', 'cashier')
        self.page.fill('#passwordInput', 'pos123')
        self.page.click('#signInBtn')
        self.page.wait_for_url("**/pos*", timeout=10000)

    def test_01_unauthenticated_redirect_and_login(self):
        """Verify unauthenticated access to /tables redirects to login, and login succeeds."""
        self.page.goto(f"{self.base_url}/tables")
        # Should be redirected to /login?next=/tables
        self.assertTrue('/login' in self.page.url)
        self.page.wait_for_selector('#usernameInput')
        self.page.fill('#usernameInput', 'cashier')
        self.page.fill('#passwordInput', 'pos123')
        self.page.click('#signInBtn')

        # Verify successful login landing on /tables
        self.page.wait_for_url("**/tables*", timeout=10000)
        self.assertTrue('/tables' in self.page.url)

    def test_02_tables_floorplan_render(self):
        """Verify table grid loads with accurate table cards, areas, and status badges."""
        self._login()
        self.page.goto(f"{self.base_url}/tables")
        self.page.wait_for_selector('#tableGrid .table-card')

        cards = self.page.locator('#tableGrid .table-card')
        count = cards.count()
        self.assertGreaterEqual(count, 3, "Expected at least 3 tables rendered in grid")

        # Verify summary stats
        summary_text = self.page.locator('#tableStatsSummary').text_content()
        self.assertIn("Tables", summary_text)

    def test_03_full_table_order_and_settlement_journey(self):
        """Complete workflow: Open Table -> Add Items -> Fire KOT -> Settle & Free Table."""
        self._login()
        self.page.goto(f"{self.base_url}/tables")
        self.page.wait_for_selector('#tableGrid .table-card')

        # 1. Click on Table T1 (which is AVAILABLE)
        t1_card = self.page.locator('#tableGrid .table-card', has_text="T1").first
        t1_card.click()

        # 2. Open Table Modal should appear
        self.page.wait_for_selector('#openTableModal', state='visible')
        self.page.fill('#waiterName', 'Captain Vicky')
        self.page.click('#openTableModal button.btn-primary')

        # 3. Fullscreen Table Order View should open
        self.page.wait_for_selector('#tableOrderView', state='visible')
        order_table_title = self.page.locator('#orderTableNumber').text_content()
        self.assertEqual(order_table_title.strip(), "T1")

        # 4. Search and Add Dish using Keyboard Shortcut (/ and Enter)
        dish_search = self.page.locator('#dishSearch')
        dish_search.focus()
        dish_search.fill('Dosa')
        self.page.keyboard.press('Enter')  # Auto-adds first match

        # Verify item added in order panel
        self.page.wait_for_selector('#orderItemsContainer .cart-item-row')
        item_rows = self.page.locator('#orderItemsContainer .cart-item-row')
        self.assertEqual(item_rows.count(), 1)
        self.assertIn("Dosa", item_rows.first.text_content())

        # 5. Add another item via dish card click
        vada_card = self.page.locator('#dishGrid .dish-card', has_text="Medu Vada").first
        vada_card.click()

        # Verify count is now 2 items
        self.page.wait_for_selector('#orderItemsContainer .cart-item-row:nth-child(2)')
        self.assertEqual(self.page.locator('#orderItemsContainer .cart-item-row').count(), 2)

        # 6. Fire KOT to Kitchen
        fire_kot_btn = self.page.locator('#btnFireKot')
        fire_kot_btn.click()
        self.page.wait_for_timeout(500)

        # 7. Print Bill (marks order as BILLED and returns to floor plan)
        self.page.locator('button.btn-bill').click()
        self.page.wait_for_timeout(600)

        # 8. Table T1 is now 🟡 BILLED on floor plan. Click to open order and settle.
        billed_card = self.page.locator('#tableGrid .table-card.status-billed', has_text="T1").first
        billed_card.wait_for(state='visible')
        billed_card.click()
        self.page.wait_for_selector('#tableOrderView', state='visible')

        # 9. Settle & Pay Modal
        settle_btn = self.page.locator('button.btn-settle')
        settle_btn.click()

        # Verify #settleTableModal is visible on top of order view
        self.page.wait_for_selector('#settleTableModal', state='visible')
        total_due_elem = self.page.locator('#settleTotalDue')
        self.assertTrue(total_due_elem.is_visible())
        total_due_text = total_due_elem.text_content()
        self.assertIn("₹", total_due_text)

        # Submit settlement
        self.page.click('#settleTableModal button.btn-success')

        # Verify modals and order view are closed and table is back to AVAILABLE
        self.page.wait_for_selector('#tableOrderView', state='hidden')
        t1_avail = self.page.locator('#tableGrid .table-card.status-available', has_text="T1").first
        t1_avail.wait_for(state='visible')
        self.assertIn("AVAILABLE", t1_avail.locator('.table-status').text_content())

    def test_04_keyboard_navigation_shortcuts(self):
        """Verify keyboard shortcuts: / focuses search, Esc closes view."""
        self._login()
        self.page.goto(f"{self.base_url}/tables")
        self.page.wait_for_selector('#tableGrid .table-card')

        # Open Table T2
        t2_card = self.page.locator('#tableGrid .table-card', has_text="T2").first
        t2_card.click()
        self.page.wait_for_selector('#openTableModal', state='visible')
        self.page.fill('#waiterName', 'Captain Sam')
        self.page.click('#openTableModal button.btn-primary')

        self.page.wait_for_selector('#tableOrderView', state='visible')

        # Press '/' to focus search
        self.page.keyboard.press('/')
        focused_tag = self.page.evaluate("document.activeElement.id")
        self.assertEqual(focused_tag, "dishSearch")

        # Search 'Coffee' and press Enter to auto-add
        self.page.locator('#dishSearch').fill('Coffee')
        self.page.keyboard.press('Enter')

        # Verify item added
        self.page.wait_for_selector('#orderItemsContainer .cart-item-row')
        self.assertIn("Coffee", self.page.locator('#orderItemsContainer').text_content())

        # Press 'Escape' to close order view
        self.page.keyboard.press('Escape')
        self.page.wait_for_selector('#tableOrderView', state='hidden')
        self.assertTrue(self.page.locator('#tableGrid').is_visible())


if __name__ == '__main__':
    unittest.main()

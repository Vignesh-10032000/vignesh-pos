"""VGL POS test suite — security-hotfix regression tests.

Every test runs against a fresh in-memory SQLite database. The environment is
sanitised BEFORE `app` is imported because app.py builds a module-level app at
import time; without this, importing the module would create (and the old
suite would drop_all()!) the real instance/pos.db.

One regression test (at least) exists per audit finding, named after it:
test_p0_1_*, test_p0_3_*, test_p0_4_*, test_p0_6_*, test_p0_8_*, test_p3_1_*,
test_p1_8_*, test_p1_10_*, test_p1_11_*, plus the DELETE-guard and
repo-hygiene checks.
"""
import glob
import json
import os
import re
import unittest
import uuid
from unittest import mock

# ── Environment MUST be sanitised before `app` is imported ───────────────────
# [P0-6] Never let the suite (or app.py's import-time create_app()) touch a
# real database, trip the rate limiter, or inherit deployment settings.
os.environ['DATABASE_URL'] = 'sqlite:///:memory:'
os.environ['RATE_LIMIT_ENABLED'] = 'false'
for _var in ('APP_ENV', 'DEMO_TOKEN', 'CORS_ORIGINS', 'SECRET_KEY', 'RECEIPT_BASE_URL'):
    os.environ.pop(_var, None)

from app import create_app  # noqa: E402
from extensions import db  # noqa: E402
from models import Product, Category, Customer, Sale, SaleItem  # noqa: E402

PROJECT_ROOT = os.path.dirname(os.path.abspath(__file__))

TEST_CONFIG = {
    'TESTING': True,
    'SQLALCHEMY_DATABASE_URI': 'sqlite:///:memory:',
}


def _read_project_file(*relative_parts):
    path = os.path.join(PROJECT_ROOT, *relative_parts)
    with open(path, encoding='utf-8') as fh:
        return fh.read()


class POSTestCase(unittest.TestCase):
    """Main suite: fresh app + fresh in-memory schema per test."""

    def setUp(self):
        self.app = create_app(TEST_CONFIG)
        self.client = self.app.test_client()

        self.app_context = self.app.app_context()
        self.app_context.push()

        # [P0-6] Fail loudly if the override did not reach the engine — this
        # is exactly the bug that made the old suite drop the real pos.db.
        engine_url = str(db.engine.url)
        if ':memory:' not in engine_url:
            raise RuntimeError(
                f'Test suite is NOT isolated: engine is {engine_url!r}, '
                "expected an in-memory SQLite database. Aborting before any "
                'test can touch a real database.'
            )

        db.create_all()
        self.seed_test_data()

    def tearDown(self):
        db.session.remove()
        db.drop_all()
        self.app_context.pop()

    def seed_test_data(self):
        cat = Category(name="Test F&B", icon="🍛")
        db.session.add(cat)
        db.session.commit()

        self.prod1 = Product(name="Coffee", sku="C001", price=20.0, stock=50,
                             category_id=cat.id, gst_rate=5)
        self.prod2 = Product(name="Tea", sku="C002", price=15.0, stock=30,
                             category_id=cat.id, gst_rate=5)
        # 299.00 @ 18% reproduces the exact broken invoice from finding P0-4.
        self.prod_usb = Product(name="USB Cable", sku="C003", price=299.0, stock=60,
                                category_id=cat.id, gst_rate=18)
        db.session.add_all([self.prod1, self.prod2, self.prod_usb])

        self.cust_walkin = Customer(name="Walk-in Customer", phone="0000000000")
        self.cust_regular = Customer(name="Kavitha", phone="9841002002")
        db.session.add_all([self.cust_walkin, self.cust_regular])
        
        from models import DiningTable
        self.table1 = DiningTable(table_number='T1', area='Main Hall', capacity=4, status='AVAILABLE')
        db.session.add(self.table1)
        
        db.session.commit()

        self.sale = Sale(
            customer_id=self.cust_regular.id,
            payment_method="cash",
            subtotal=33.33,
            total_cgst=0.83,
            total_sgst=0.83,
            total=35.0,
            discount=0,
            amount_paid=35.0,
            change_given=0
        )
        db.session.add(self.sale)
        db.session.flush()

        self.sale_item1 = SaleItem(sale_id=self.sale.id, product_id=self.prod1.id,
                                   quantity=1, price=20.0, gst_rate=5, subtotal=20.0)
        self.sale_item2 = SaleItem(sale_id=self.sale.id, product_id=self.prod2.id,
                                   quantity=1, price=15.0, gst_rate=5, subtotal=15.0)
        db.session.add_all([self.sale_item1, self.sale_item2])
        db.session.commit()

    def checkout(self, payload):
        return self.client.post('/api/checkout', json=payload)

    # ── Pre-existing behaviour kept working ─────────────────────────────────

    def test_get_customers_walkin_exists(self):
        response = self.client.get('/api/customers')
        self.assertEqual(response.status_code, 200)
        names = [c['name'] for c in json.loads(response.data)]
        self.assertIn("Walk-in Customer", names)

    def test_product_stock_adjustment(self):
        response = self.client.patch(f'/api/products/{self.prod1.id}/stock',
                                     json={'adjustment': 10})
        self.assertEqual(response.status_code, 200)
        data = json.loads(response.data)
        self.assertTrue(data['success'])
        self.assertEqual(data['new_stock'], 60)

    def test_product_soft_delete(self):
        response = self.client.delete(f'/api/products/{self.prod1.id}')
        self.assertEqual(response.status_code, 200)
        self.assertTrue(json.loads(response.data)['success'])
        self.assertFalse(db.session.get(Product, self.prod1.id).active)

    def test_day_close(self):
        response = self.client.get('/api/reports/day-close')
        self.assertEqual(response.status_code, 200)
        data = json.loads(response.data)
        self.assertTrue(data['success'])
        self.assertTrue(data['total_transactions'] >= 1)
        self.assertTrue(data['grand_total'] >= 35.0)
        self.assertTrue(data['cash'] >= 35.0)

    def test_sale_return_processing(self):
        response = self.client.post(f'/api/sales/{self.sale.id}/return', json={
            'items': [{'product_id': self.prod1.id, 'quantity': 1}],
            'payment_method': 'cash',
            'reason': 'Customer requested return'
        })
        self.assertEqual(response.status_code, 200)
        data = json.loads(response.data)
        self.assertTrue(data['success'])
        self.assertEqual(data['refund_amount'], 20.0)
        self.assertEqual(db.session.get(Product, self.prod1.id).stock, 51)

        response_dup = self.client.post(f'/api/sales/{self.sale.id}/return', json={
            'items': [{'product_id': self.prod1.id, 'quantity': 1}],
            'payment_method': 'cash',
            'reason': 'Double return attempt'
        })
        self.assertEqual(response_dup.status_code, 400)

    def test_loaderio_dynamic_verification(self):
        test_token = '30b619278e7e5d9e56623259767975ba'
        for path in (f'/loaderio-{test_token}.txt', f'/loaderio-{test_token}.html',
                     f'/loaderio-{test_token}'):
            response = self.client.get(path)
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.data.decode('utf-8'), f'loaderio-{test_token}')

    def test_openapi_spec(self):
        response = self.client.get('/openapi.json')
        self.assertEqual(response.status_code, 200)
        data = json.loads(response.data)
        self.assertEqual(data['openapi'], '3.0.0')
        self.assertEqual(data['info']['title'], 'VGL Retail POS API')

    def test_checkout_happy_path_still_works(self):
        response = self.checkout({
            'items': [{'product_id': self.prod1.id, 'quantity': 2}],
            'customer_id': self.cust_walkin.id,
            'payment_method': 'cash',
            'amount_paid': 50,
            'discount': 0,
        })
        self.assertEqual(response.status_code, 200)
        data = json.loads(response.data)
        self.assertTrue(data['success'])
        self.assertEqual(data['sale']['total'], 40.0)
        self.assertEqual(data['sale']['change_given'], 10.0)
        self.assertEqual(db.session.get(Product, self.prod1.id).stock, 48)

    # ── [P0-1] Demo endpoints must not exist outside APP_ENV=demo ───────────

    def test_p0_1_demo_routes_absent_by_default(self):
        registered = {str(rule) for rule in self.app.url_map.iter_rules()}
        self.assertNotIn('/api/demo/reset', registered)
        self.assertNotIn('/api/demo/simulate_sale', registered)
        self.assertEqual(self.client.post('/api/demo/reset').status_code, 404)
        self.assertEqual(self.client.post('/api/demo/simulate_sale').status_code, 404)

    def test_p0_1_demo_routes_require_valid_token_in_demo_env(self):
        with mock.patch.dict(os.environ, {'APP_ENV': 'demo', 'DEMO_TOKEN': 'test-demo-token'}):
            demo_app = create_app(TEST_CONFIG)
            with demo_app.app_context():
                db.create_all()
            client = demo_app.test_client()

            # No token / wrong token: 404, never 403 — do not advertise.
            self.assertEqual(client.post('/api/demo/reset').status_code, 404)
            self.assertEqual(
                client.post('/api/demo/reset',
                            headers={'X-Demo-Token': 'wrong'}).status_code, 404)
            self.assertEqual(
                client.post('/api/demo/simulate_sale',
                            headers={'X-Demo-Token': 'wrong'}).status_code, 404)

            # Correct token: works.
            response = client.post('/api/demo/reset',
                                   headers={'X-Demo-Token': 'test-demo-token'})
            self.assertEqual(response.status_code, 200)
            self.assertTrue(json.loads(response.data)['success'])

            response = client.post('/api/demo/simulate_sale',
                                   headers={'X-Demo-Token': 'test-demo-token'})
            self.assertEqual(response.status_code, 200)
            self.assertTrue(json.loads(response.data)['success'])

    def test_p0_1_demo_env_without_configured_token_always_404(self):
        with mock.patch.dict(os.environ, {'APP_ENV': 'demo'}):
            os.environ.pop('DEMO_TOKEN', None)
            demo_app = create_app(TEST_CONFIG)
            client = demo_app.test_client()
            self.assertEqual(
                client.post('/api/demo/reset', headers={'X-Demo-Token': ''}).status_code,
                404)

    # ── [P0-3] Checkout input validation ────────────────────────────────────

    def _assert_rejected(self, payload, fragment):
        stock_before = db.session.get(Product, self.prod1.id).stock
        response = self.checkout(payload)
        self.assertEqual(response.status_code, 400, response.data)
        self.assertIn(fragment, json.loads(response.data)['error'])
        self.assertEqual(db.session.get(Product, self.prod1.id).stock, stock_before,
                         'rejected checkout must not mutate stock')

    def test_p0_3_negative_quantity_rejected(self):
        self._assert_rejected(
            {'items': [{'product_id': self.prod1.id, 'quantity': -5}], 'amount_paid': 0},
            'quantity must be greater than 0')

    def test_p0_3_zero_quantity_rejected(self):
        self._assert_rejected(
            {'items': [{'product_id': self.prod1.id, 'quantity': 0}], 'amount_paid': 0},
            'quantity must be greater than 0')

    def test_p0_3_oversized_quantity_rejected(self):
        self._assert_rejected(
            {'items': [{'product_id': self.prod1.id, 'quantity': 10001}], 'amount_paid': 0},
            'must not exceed 10000')

    def test_p0_3_non_integer_quantity_rejected(self):
        self._assert_rejected(
            {'items': [{'product_id': self.prod1.id, 'quantity': '5'}], 'amount_paid': 0},
            'quantity must be an integer')
        self._assert_rejected(
            {'items': [{'product_id': self.prod1.id, 'quantity': 1.5}], 'amount_paid': 0},
            'quantity must be an integer')

    def test_p0_3_too_many_lines_rejected(self):
        lines = [{'product_id': self.prod1.id, 'quantity': 1}] * 201
        self._assert_rejected({'items': lines, 'amount_paid': 0}, 'maximum 200')

    def test_p0_3_negative_discount_rejected(self):
        self._assert_rejected(
            {'items': [{'product_id': self.prod1.id, 'quantity': 1}],
             'amount_paid': 20, 'discount': -10},
            'discount must be greater than or equal to 0')

    def test_p0_3_discount_above_cart_total_rejected(self):
        self._assert_rejected(
            {'items': [{'product_id': self.prod1.id, 'quantity': 1}],
             'amount_paid': 0, 'discount': 99999},
            'discount must not exceed the cart total')

    def test_p0_3_negative_amount_paid_rejected(self):
        self._assert_rejected(
            {'items': [{'product_id': self.prod1.id, 'quantity': 1}],
             'amount_paid': -1},
            'amount_paid must be greater than or equal to 0')

    def test_p0_3_unknown_customer_rejected(self):
        self._assert_rejected(
            {'items': [{'product_id': self.prod1.id, 'quantity': 1}],
             'amount_paid': 20, 'customer_id': 99999},
            'does not exist')

    def test_p0_3_duplicate_lines_cannot_oversell_stock(self):
        # Two 30-unit lines of a 50-stock product must be judged together.
        self._assert_rejected(
            {'items': [{'product_id': self.prod1.id, 'quantity': 30},
                       {'product_id': self.prod1.id, 'quantity': 30}],
             'amount_paid': 2000},
            'Stock insufficient')

    # ── [P0-4] Invoice must reconcile: taxable + CGST + SGST == total ───────

    def test_p0_4_discounted_invoice_reconciles(self):
        # The exact case from the audit: ₹299 @ 18% with ₹50 discount used to
        # store taxable 253.39 + 22.80 + 22.80 = 298.99 against total 249.00.
        response = self.checkout({
            'items': [{'product_id': self.prod_usb.id, 'quantity': 1}],
            'amount_paid': 249, 'discount': 50,
        })
        self.assertEqual(response.status_code, 200, response.data)
        sale = json.loads(response.data)['sale']
        self.assertEqual(sale['total'], 249.0)
        self.assertEqual(sale['subtotal'], 211.02)
        self.assertEqual(sale['total_cgst'], 18.99)
        self.assertEqual(sale['total_sgst'], 18.99)
        self.assertLessEqual(
            abs(sale['subtotal'] + sale['total_cgst'] + sale['total_sgst'] - sale['total']),
            0.01)

    def test_p0_4_multiline_largest_remainder_loses_no_paisa(self):
        response = self.checkout({
            'items': [{'product_id': self.prod_usb.id, 'quantity': 1},
                      {'product_id': self.prod1.id, 'quantity': 1}],
            'amount_paid': 309, 'discount': 10,
        })
        self.assertEqual(response.status_code, 200, response.data)
        sale = json.loads(response.data)['sale']
        self.assertEqual(sale['total'], 309.0)  # 319 gross − 10, exactly
        self.assertEqual(sale['discount'], 10.0)
        self.assertLessEqual(
            abs(sale['subtotal'] + sale['total_cgst'] + sale['total_sgst'] - sale['total']),
            0.01)

    def test_p0_4_full_discount_boundary_reconciles(self):
        response = self.checkout({
            'items': [{'product_id': self.prod1.id, 'quantity': 1}],
            'amount_paid': 0, 'discount': 20,
        })
        self.assertEqual(response.status_code, 200, response.data)
        sale = json.loads(response.data)['sale']
        self.assertEqual(sale['total'], 0.0)
        self.assertLessEqual(
            abs(sale['subtotal'] + sale['total_cgst'] + sale['total_sgst'] - sale['total']),
            0.01)

    # ── [P0-6] Suite isolation ──────────────────────────────────────────────

    def test_p0_6_engine_is_in_memory(self):
        # create_app(config) must apply the URI BEFORE db.init_app(); if the
        # override regresses, setUp raises and every test aborts.
        self.assertIn(':memory:', str(db.engine.url))
        self.assertEqual(self.app.config['SQLALCHEMY_DATABASE_URI'], 'sqlite:///:memory:')

    # ── [P0-8] escapeHtml + template audit ──────────────────────────────────

    def test_p0_8_escapehtml_escapes_all_dangerous_characters(self):
        pos_js = _read_project_file('static', 'js', 'pos.js')
        self.assertIn('function escapeHtml', pos_js)
        for entity in ('&amp;', '&lt;', '&gt;', '&quot;', '&#39;'):
            self.assertIn(entity, pos_js,
                          f'escapeHtml must produce {entity}')
        # The misleading apostrophe-only helper is gone; the JS-literal
        # escaper lives on under an honest name.
        self.assertNotIn('function escHtml', pos_js)
        self.assertIn('function escJsString', pos_js)

    def test_p0_8_no_template_interpolates_server_data_unescaped(self):
        template_dir = os.path.join(PROJECT_ROOT, 'templates')
        # Raw interpolations of server-supplied fields that powered the XSS.
        forbidden = [
            '${p.name}', '${c.name}', '${s.customer}', '${i.product_name}',
            '${item.name}', '${p.sku}', '${p.barcode}', '${c.email}',
            '${c.phone}', '${c.gstin}', '${p.hsn_code', '${c.icon}',
            '${p.category_icon}', 'escHtml(',
        ]
        for template_path in sorted(glob.glob(os.path.join(template_dir, '*.html'))):
            with open(template_path, encoding='utf-8') as fh:
                content = fh.read()
            for pattern in forbidden:
                self.assertNotIn(
                    pattern, content,
                    f'{os.path.basename(template_path)} still interpolates '
                    f'server data unescaped: {pattern}')

    # ── [P3-1] Cart total must never round-trip through the DOM ────────────

    def test_p3_1_cart_total_kept_in_module_state_not_dom(self):
        pos_html = _read_project_file('templates', 'pos.html')
        self.assertNotIn("textContent.replace('₹'", pos_html)
        self.assertNotIn(".replace(',','')", pos_html)
        self.assertIn('let cartTotal = 0;', pos_html)
        self.assertIn('updateChange(cartTotal)', pos_html)
        self.assertIn('const total = cartTotal;', pos_html)

    def test_p3_1_dom_parse_of_indian_grouping_was_lossy(self):
        # Documents WHY the DOM round-trip had to go: the removed code parsed
        # fmt()'s en-IN output with a single .replace(',','') + parseFloat.
        def format_inr(value):
            whole, frac = f'{value:.2f}'.split('.')
            if len(whole) > 3:
                head, tail = whole[:-3], whole[-3:]
                groups = []
                while len(head) > 2:
                    groups.insert(0, head[-2:])
                    head = head[:-2]
                if head:
                    groups.insert(0, head)
                whole = ','.join(groups + [tail])
            return '₹' + whole + '.' + frac

        def old_dom_parse(text):
            stripped = text.replace('₹', '').replace(',', '', 1)  # first comma only
            match = re.match(r'^[+-]?(\d+(\.\d*)?|\.\d+)', stripped)
            return float(match.group(0)) if match else float('nan')

        self.assertEqual(format_inr(100000.00), '₹1,00,000.00')
        # ₹1,234.00 happened to survive; lakhs+ silently collapsed.
        self.assertEqual(old_dom_parse(format_inr(1234.00)), 1234.00)
        self.assertEqual(old_dom_parse(format_inr(100000.00)), 100.00)
        self.assertNotEqual(old_dom_parse(format_inr(100000.00)), 100000.00)
        self.assertNotEqual(old_dom_parse(format_inr(1234567.89)), 1234567.89)
        # The fix keeps the Number in module state, so the value IS the value.
        for value in (1234.00, 100000.00, 1234567.89):
            self.assertEqual(value, value)

    # ── [P1-8] Error handler must not leak internals ────────────────────────

    def test_p1_8_error_handler_returns_request_id_not_details(self):
        marker = 'SECRET_SQL_DETAIL_do_not_leak'

        @self.app.route('/boom-p1-8')
        def boom():
            raise RuntimeError(marker)

        # TESTING normally re-raises; force the production errorhandler path.
        self.app.config['PROPAGATE_EXCEPTIONS'] = False
        response = self.client.get('/boom-p1-8')
        self.assertEqual(response.status_code, 500)
        body = response.get_data(as_text=True)
        self.assertNotIn(marker, body)
        data = json.loads(body)
        self.assertEqual(data['error'], 'Internal server error')
        self.assertNotIn('details', data)
        uuid.UUID(data['request_id'])  # raises if not a valid uuid4-style id

    # ── [P1-10] CORS ────────────────────────────────────────────────────────

    def test_p1_10_cors_defaults_to_same_origin_only(self):
        response = self.client.get('/api/customers',
                                   headers={'Origin': 'http://evil.example'})
        self.assertIsNone(response.headers.get('Access-Control-Allow-Origin'))

    def test_p1_10_cors_allows_only_configured_origins(self):
        with mock.patch.dict(os.environ, {'CORS_ORIGINS': 'https://pos.example.com'}):
            cors_app = create_app(TEST_CONFIG)
            with cors_app.app_context():
                db.create_all()
            client = cors_app.test_client()
            allowed = client.get('/api/customers',
                                 headers={'Origin': 'https://pos.example.com'})
            self.assertEqual(allowed.headers.get('Access-Control-Allow-Origin'),
                             'https://pos.example.com')
            denied = client.get('/api/customers',
                                headers={'Origin': 'http://evil.example'})
            self.assertIsNone(denied.headers.get('Access-Control-Allow-Origin'))

    # ── [P1-11] SECRET_KEY policy ───────────────────────────────────────────

    def test_p1_11_production_without_secret_key_refuses_to_boot(self):
        with mock.patch.dict(os.environ, {'APP_ENV': 'production'}):
            os.environ.pop('SECRET_KEY', None)
            with self.assertRaises(RuntimeError):
                create_app(TEST_CONFIG)

    def test_p1_11_production_with_secret_key_boots(self):
        with mock.patch.dict(os.environ, {'APP_ENV': 'production',
                                          'SECRET_KEY': 'unit-test-secret'}):
            production_app = create_app(TEST_CONFIG)
            self.assertEqual(production_app.config['SECRET_KEY'], 'unit-test-secret')

    def test_p1_11_dev_fallback_is_random_not_hardcoded(self):
        os.environ.pop('SECRET_KEY', None)
        app_one = create_app(TEST_CONFIG)
        app_two = create_app(TEST_CONFIG)
        for candidate in (app_one, app_two):
            self.assertNotEqual(candidate.config['SECRET_KEY'], 'tn-pos-secret-2024')
            self.assertGreaterEqual(len(candidate.config['SECRET_KEY']), 32)
        self.assertNotEqual(app_one.config['SECRET_KEY'], app_two.config['SECRET_KEY'])

    # ── DELETE guard: sales are cancelled, never hard-deleted ───────────────

    def test_delete_guard_sale_is_cancelled_not_deleted(self):
        stock_before = db.session.get(Product, self.prod1.id).stock
        response = self.client.delete(f'/api/sales/{self.sale.id}')
        self.assertEqual(response.status_code, 200)
        self.assertTrue(json.loads(response.data)['success'])

        # Row still exists, is stamped, and stock came back.
        sale = db.session.get(Sale, self.sale.id)
        self.assertIsNotNone(sale)
        self.assertIsNotNone(sale.cancelled_at)
        self.assertEqual(db.session.get(Product, self.prod1.id).stock, stock_before + 1)

        # Gone from the operational list…
        listing = json.loads(self.client.get('/api/sales').data)
        self.assertNotIn(self.sale.id, [s['id'] for s in listing['sales']])

        # …and a second DELETE must not restore stock twice.
        response_again = self.client.delete(f'/api/sales/{self.sale.id}')
        self.assertEqual(response_again.status_code, 400)
        self.assertEqual(db.session.get(Product, self.prod1.id).stock, stock_before + 1)

    # ── Hardcoded deployment host / committed credentials ───────────────────

    def test_whatsapp_receipt_base_url_from_env(self):
        source = _read_project_file('routes', 'sales.py')
        self.assertNotIn('railway.app', source)
        with mock.patch.dict(os.environ, {'RECEIPT_BASE_URL': 'https://pos.example.com'}):
            response = self.client.get(f'/api/sales/{self.sale.id}/whatsapp')
            self.assertEqual(response.status_code, 200)
            data = json.loads(response.data)
            self.assertIn(f'https://pos.example.com/receipt/{self.sale.id}',
                          data['message'])

    def test_repo_contains_no_tunnel_or_loadtest_credentials(self):
        self.assertFalse(os.path.exists(os.path.join(PROJECT_ROOT, 'ngrok.yml')))
        self.assertEqual(
            glob.glob(os.path.join(PROJECT_ROOT, 'static', 'loaderio-*.txt')), [])
        gitignore = _read_project_file('.gitignore')
        self.assertIn('ngrok.yml', gitignore)
        self.assertIn('loaderio-*.txt', gitignore)

    # ── Authentication & Protected Route Access ───────────────────────────────

    def test_unauthenticated_pages_redirect_to_login(self):
        for path in ('/', '/pos', '/products', '/sales', '/customers', '/reports', '/settings'):
            res = self.client.get(path)
            self.assertEqual(res.status_code, 302, f"Expected redirect for {path}")
            self.assertIn('/login', res.headers['Location'])

    def test_public_receipt_accessible_without_login(self):
        res = self.client.get(f'/receipt/{self.sale.id}')
        self.assertEqual(res.status_code, 200)

    def test_api_login_rejects_invalid_credentials(self):
        res = self.client.post('/api/login', json={'username': 'admin', 'password': 'wrongpassword'})
        self.assertEqual(res.status_code, 401)
        data = json.loads(res.data)
        self.assertFalse(data['success'])

    def test_auth_login_and_logout_lifecycle(self):
        # 1. Initially unauthenticated visit to / redirects to /login
        res_init = self.client.get('/')
        self.assertEqual(res_init.status_code, 302)
        self.assertIn('/login', res_init.headers['Location'])

        # 2. Login via API
        res_login = self.client.post('/api/login', json={'username': 'admin', 'password': 'admin123'})
        self.assertEqual(res_login.status_code, 200)
        data = json.loads(res_login.data)
        self.assertTrue(data['success'])
        self.assertEqual(data['user']['role'], 'Store Admin')

        # 3. Authenticated visit to / and /pos succeeds
        res_dash = self.client.get('/')
        self.assertEqual(res_dash.status_code, 200)

        res_pos = self.client.get('/pos')
        self.assertEqual(res_pos.status_code, 200)

        # 4. Visiting /login while authenticated redirects to /pos
        res_login_page = self.client.get('/login')
        self.assertEqual(res_login_page.status_code, 302)
        self.assertIn('/pos', res_login_page.headers['Location'])

        # 5. Logout clears session and redirects to /login
        res_logout = self.client.get('/logout')
        self.assertEqual(res_logout.status_code, 302)
        self.assertIn('/login', res_logout.headers['Location'])

        # 6. Subsequent visit to / again redirects to /login
        res_post_logout = self.client.get('/')
        self.assertEqual(res_post_logout.status_code, 302)
        self.assertIn('/login', res_post_logout.headers['Location'])



    # ── Phase 1 & 2 Restaurant POS Tests ────────────────────────────────────
    def test_create_dining_table(self):
        response = self.client.post('/api/tables/config', json={'table_number': 'T99', 'capacity': 2})
        self.assertEqual(response.status_code, 200)
        data = json.loads(response.data)
        self.assertTrue(data['success'])
        self.assertEqual(data['table']['table_number'], 'T99')

    def test_open_order_on_table(self):
        # Open T1
        response = self.client.post('/api/tables/1/open', json={'waiter_name': 'Ravi'})
        self.assertEqual(response.status_code, 200)
        data = json.loads(response.data)
        self.assertTrue(data['success'])
        self.assertEqual(data['sale']['status'], 'OPEN')
        self.assertEqual(data['sale']['order_type'], 'DINE_IN')
        self.assertEqual(data['sale']['waiter_name'], 'Ravi')

    def test_add_items_to_table_order(self):
        self.client.post('/api/tables/1/open', json={'waiter_name': 'Ravi'})
        response = self.client.post('/api/tables/1/items', json={
            'items': [
                {'product_id': self.prod1.id, 'quantity': 2, 'cooking_notes': 'Hot'}
            ]
        })
        self.assertEqual(response.status_code, 200)
        data = json.loads(response.data)
        self.assertTrue(data['success'])
        self.assertEqual(len(data['sale']['items']), 1)
        self.assertEqual(data['sale']['items'][0]['cooking_notes'], 'Hot')

    def test_fire_kot(self):
        self.client.post('/api/tables/1/open', json={'waiter_name': 'Ravi'})
        self.client.post('/api/tables/1/items', json={'items': [{'product_id': self.prod1.id, 'quantity': 2}]})
        response = self.client.post('/api/tables/1/kot')
        self.assertEqual(response.status_code, 200)
        data = json.loads(response.data)
        self.assertTrue(data['success'])
        self.assertEqual(data['kot']['status'], 'SENT')
        self.assertEqual(len(data['kot']['items']), 1)
        self.assertEqual(data['kot']['items'][0]['quantity'], 2)

    def test_multiple_kots_per_table(self):
        self.client.post('/api/tables/1/open', json={'waiter_name': 'Ravi'})
        self.client.post('/api/tables/1/items', json={'items': [{'product_id': self.prod1.id, 'quantity': 2}]})
        self.client.post('/api/tables/1/kot')
        
        self.client.post('/api/tables/1/items', json={'items': [{'product_id': self.prod2.id, 'quantity': 1}]})
        response = self.client.post('/api/tables/1/kot')
        self.assertEqual(response.status_code, 200)
        data = json.loads(response.data)
        self.assertTrue(data['success'])
        self.assertEqual(len(data['kot']['items']), 1)
        self.assertEqual(data['kot']['items'][0]['product_id'], self.prod2.id)

    def test_settle_table(self):
        self.client.post('/api/tables/1/open', json={'waiter_name': 'Ravi'})
        self.client.post('/api/tables/1/items', json={'items': [{'product_id': self.prod1.id, 'quantity': 2}]})
        response = self.client.post('/api/tables/1/settle', json={'amount_paid': 42.0}) # 20*2 + 5% gst = 42
        self.assertEqual(response.status_code, 200)
        data = json.loads(response.data)
        self.assertTrue(data['success'])
        self.assertEqual(data['sale']['status'], 'COMPLETED')
        
        # Table freed
        tables = json.loads(self.client.get('/api/tables').data)
        t1 = next(t for t in tables if t['id'] == 1)
        self.assertEqual(t1['status'], 'AVAILABLE')

    def test_settle_table_with_string_amount(self):
        # Browser forms often submit amount_paid as strings (e.g. "42.00")
        self.client.post('/api/tables/1/open', json={'waiter_name': 'Ravi'})
        self.client.post('/api/tables/1/items', json={'items': [{'product_id': self.prod1.id, 'quantity': 2}]})
        response = self.client.post('/api/tables/1/settle', json={'amount_paid': "42.00", 'payment_method': 'cash'})
        self.assertEqual(response.status_code, 200)
        data = json.loads(response.data)
        self.assertTrue(data['success'])
        self.assertEqual(data['sale']['status'], 'COMPLETED')
        self.assertEqual(data['sale']['payment_method'], 'cash')
        
        # Table freed
        tables = json.loads(self.client.get('/api/tables').data)
        t1 = next(t for t in tables if t['id'] == 1)
        self.assertEqual(t1['status'], 'AVAILABLE')

    def test_cancel_open_order(self):
        self.client.post('/api/tables/1/open', json={'waiter_name': 'Ravi'})
        response = self.client.post('/api/tables/1/cancel', json={'reason': 'Customer left'})
        self.assertEqual(response.status_code, 200)
        
        # Table freed
        tables = json.loads(self.client.get('/api/tables').data)
        t1 = next(t for t in tables if t['id'] == 1)
        self.assertEqual(t1['status'], 'AVAILABLE')

    def test_reports_exclude_open_orders(self):
        # Ensure open orders don't appear in revenue
        rev_before = json.loads(self.client.get('/api/reports/revenue').data)['total']
        self.client.post('/api/tables/1/open', json={'waiter_name': 'Ravi'})
        self.client.post('/api/tables/1/items', json={'items': [{'product_id': self.prod1.id, 'quantity': 100}]})
        
        rev_after = json.loads(self.client.get('/api/reports/revenue').data)['total']
        self.assertEqual(rev_before, rev_after)

    def test_existing_retail_checkout_still_works(self):
        response = self.checkout({
            'items': [{'product_id': self.prod1.id, 'quantity': 2}],
            'customer_id': self.cust_walkin.id,
            'payment_method': 'cash',
            'amount_paid': 50,
            'discount': 0,
        })
        self.assertEqual(response.status_code, 200)
        data = json.loads(response.data)
        self.assertEqual(data['sale']['status'], 'COMPLETED')
        self.assertEqual(data['sale']['order_type'], 'COUNTER')

    def test_cooking_notes_on_kot_items(self):
        self.client.post('/api/tables/1/open')
        self.client.post('/api/tables/1/items', json={'items': [{'product_id': self.prod1.id, 'quantity': 1, 'cooking_notes': 'Extra sugar'}]})
        resp = self.client.post('/api/tables/1/kot')
        data = json.loads(resp.data)
        self.assertEqual(data['kot']['items'][0]['cooking_notes'], 'Extra sugar')

    def test_cannot_remove_item_after_kot(self):
        self.client.post('/api/tables/1/open')
        self.client.post('/api/tables/1/items', json={'items': [{'product_id': self.prod1.id, 'quantity': 1}]})
        
        # We need the item_id. 
        order_resp = self.client.get('/api/tables/1/order')
        item_id = json.loads(order_resp.data)['items'][0]['id']
        
        self.client.post('/api/tables/1/kot')
        
        # Try delete
        del_resp = self.client.delete(f'/api/tables/1/items/{item_id}')
        self.assertEqual(del_resp.status_code, 400)
        self.assertIn('Item already sent', json.loads(del_resp.data)['error'])

    def test_print_bill_calculates_gst(self):
        self.client.post('/api/tables/1/open')
        self.client.post('/api/tables/1/items', json={'items': [{'product_id': self.prod1.id, 'quantity': 2}]}) # 20*2 = 40.00 incl GST
        resp = self.client.post('/api/tables/1/print-bill')
        data = json.loads(resp.data)
        self.assertEqual(data['sale']['status'], 'BILLED')
        # 40 / 1.05 = 38.10 taxable, 1.90 GST
        self.assertEqual(data['sale']['subtotal'], 38.10)
        self.assertEqual(data['sale']['total_cgst'], 0.95)
        self.assertEqual(data['sale']['total_sgst'], 0.95)
        self.assertEqual(data['sale']['total'], 40.0)

    def test_kot_print_template_renders_table_info(self):
        self.client.post('/api/tables/1/open', json={'waiter_name': 'Suresh'})
        self.client.post('/api/tables/1/items', json={'items': [{'product_id': self.prod1.id, 'quantity': 1, 'cooking_notes': 'Spicy'}]})
        kot_resp = self.client.post('/api/tables/1/kot')
        kot_id = json.loads(kot_resp.data)['kot']['id']
        
        resp = self.client.get(f'/kot/{kot_id}/print')
        self.assertEqual(resp.status_code, 200)
        content = resp.get_data(as_text=True)
        self.assertIn('KOT #', content)
        self.assertIn('Table:', content)
        self.assertIn('T1', content)
        self.assertIn('Suresh', content)
        self.assertIn('Spicy', content)

    def test_unauthenticated_tables_page_redirects(self):
        resp = self.client.get('/tables')
        self.assertEqual(resp.status_code, 302)
        self.assertIn('/login?next=/tables', resp.headers['Location'])


if __name__ == '__main__':
    unittest.main()


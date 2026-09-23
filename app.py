import os
import sys
import time
import hmac
import uuid
import secrets
import logging
import psutil
from flask import Flask, abort, jsonify, request, session, redirect
from flask_compress import Compress
from flask_limiter import Limiter
from flask_limiter.util import get_remote_address
from flask_cors import CORS
from sqlalchemy import event
from sqlalchemy.engine import Engine
from extensions import db, cache
from datetime import datetime, timedelta

# ── SQLite Optimization (WAL mode, normal synchronous, busy timeout) ─────────
@event.listens_for(Engine, "connect")
def set_sqlite_pragma(dbapi_connection, connection_record):
    # Only run SQLite pragmas if the DB API connection is native SQLite3
    if type(dbapi_connection).__module__.startswith("sqlite3"):
        cursor = dbapi_connection.cursor()
        try:
            cursor.execute("PRAGMA journal_mode=WAL")
            cursor.execute("PRAGMA synchronous=NORMAL")
            cursor.execute("PRAGMA busy_timeout=5000")
        except Exception:
            pass
        finally:
            cursor.close()


# ── Metrics Tracker ──────────────────────────────────────────────────────────
class MetricsTracker:
    def __init__(self):
        self.start_time = time.time()
        self.total_requests = 0
        self.total_response_time = 0.0
        self.active_connections = 0

metrics = MetricsTracker()

# Setup Logger mimicking Morgan format
logging.basicConfig(level=logging.INFO)
morgan_logger = logging.getLogger('morgan')

# Setup Rate Limiter (Default: 100 requests per minute per IP, configurable for load tests)
rate_limit_enabled = os.environ.get('RATE_LIMIT_ENABLED', 'true').lower() in ('true', '1', 'yes')
default_rate_limit = os.environ.get('RATE_LIMIT_DEFAULT', '100 per minute')

limiter = Limiter(
    key_func=get_remote_address,
    default_limits=[default_rate_limit],
    enabled=rate_limit_enabled,
    storage_uri="memory://"
)

STORE_NAME = "Vignesh Growth Lab POS"
STORE_NAME_EN = "Vignesh Growth Lab POS"
STORE_GSTIN = "33AABCU9603R1ZX"          # Tamil Nadu GSTIN (33 = TN)
STORE_ADDRESS = "23, Anna Salai, Chennai - 600002, Tamil Nadu"
STORE_PHONE = "+91 98400 00001"


def create_app(config=None):
    if getattr(sys, 'frozen', False):
        base_path = sys._MEIPASS
        app = Flask(__name__, 
                    template_folder=os.path.join(base_path, 'templates'),
                    static_folder=os.path.join(base_path, 'static'))
    else:
        app = Flask(__name__)
        
    database_url = os.environ.get('DATABASE_URL')
    if database_url:
        if database_url.startswith("postgres://"):
            database_url = database_url.replace("postgres://", "postgresql://", 1)
        app.config['SQLALCHEMY_DATABASE_URI'] = database_url
    else:
        app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///pos.db'
    app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
    app.config['SECRET_KEY'] = os.environ.get('SECRET_KEY')
    app.config['STORE_NAME'] = STORE_NAME
    app.config['STORE_NAME_EN'] = STORE_NAME_EN
    app.config['STORE_GSTIN'] = STORE_GSTIN
    app.config['STORE_ADDRESS'] = STORE_ADDRESS
    app.config['STORE_PHONE'] = STORE_PHONE
    app.config['PERMANENT_SESSION_LIFETIME'] = timedelta(days=7)

    # [P0-6] Test/override config must be applied BEFORE db.init_app(), because
    # Flask-SQLAlchemy 3.x builds its engine inside init_app(). Overriding
    # SQLALCHEMY_DATABASE_URI after create_app() has no effect.
    if config is not None:
        if isinstance(config, dict):
            app.config.update(config)
        else:
            app.config.from_object(config)

    # [P1-11] Never boot production with a missing/hardcoded SECRET_KEY.
    if not app.config.get('SECRET_KEY'):
        if os.environ.get('APP_ENV') == 'production':
            raise RuntimeError(
                'SECRET_KEY environment variable is not set. Refusing to start '
                'in production without an explicit SECRET_KEY.'
            )
        # Dev fallback: random per-process key (sessions do not survive restarts).
        app.config['SECRET_KEY'] = secrets.token_hex(32)

    db.init_app(app)
    cache.init_app(app, config={'CACHE_TYPE': 'SimpleCache'})
    Compress(app)
    limiter.init_app(app)
    # [P1-10] Allow only origins listed in CORS_ORIGINS (comma-separated).
    # Empty/unset means same-origin only: no CORS headers are emitted at all.
    cors_origins = [o.strip() for o in os.environ.get('CORS_ORIGINS', '').split(',') if o.strip()]
    CORS(app, origins=cors_origins)


    from routes.dashboard import dashboard_bp
    from routes.pos import pos_bp
    from routes.products import products_bp
    from routes.sales import sales_bp
    from routes.customers import customers_bp
    from routes.reports import reports_bp
    from routes.tables import tables_bp

    app.register_blueprint(dashboard_bp)
    app.register_blueprint(pos_bp)
    app.register_blueprint(products_bp)
    app.register_blueprint(sales_bp)
    app.register_blueprint(customers_bp)
    app.register_blueprint(reports_bp)
    app.register_blueprint(tables_bp)

    PROTECTED_PAGES = {
        '/',
        '/pos',
        '/tables',
        '/products',
        '/sales',
        '/customers',
        '/reports',
        '/settings',
    }

    # Request tracking and session authentication hooks
    @app.before_request
    def before_request():
        request.start_time = time.time()
        metrics.total_requests += 1
        metrics.active_connections += 1

        path = request.path.rstrip('/') or '/'
        if path in PROTECTED_PAGES and not session.get('user'):
            return redirect(f'/login?next={request.path}')

    @app.after_request
    def after_request(response):
        metrics.active_connections = max(0, metrics.active_connections - 1)
        if hasattr(request, 'start_time'):
            duration = time.time() - request.start_time
            metrics.total_response_time += duration
            
            # Performance timing header
            response.headers['X-Response-Time-Ms'] = f"{duration * 1000:.2f}"
            
            # Prevent static caching issues during active development/customization
            if request.path.startswith('/static/'):
                response.headers['Cache-Control'] = 'no-cache, no-store, must-revalidate, max-age=0'
                response.headers['Pragma'] = 'no-cache'
                response.headers['Expires'] = '0'
            else:
                response.headers['Cache-Control'] = 'no-store, no-cache, must-revalidate, max-age=0'
                
            # Helmet security headers (Csp, frame options, etc)
            response.headers['X-Content-Type-Options'] = 'nosniff'
            response.headers['X-Frame-Options'] = 'SAMEORIGIN'
            response.headers['X-XSS-Protection'] = '1; mode=block'
            response.headers['Strict-Transport-Security'] = 'max-age=31536000; includeSubDomains'
            response.headers['Content-Security-Policy'] = "default-src 'self' https://cdn.jsdelivr.net https://cdnjs.cloudflare.com; script-src 'self' 'unsafe-inline' https://cdn.jsdelivr.net https://cdnjs.cloudflare.com; style-src 'self' 'unsafe-inline' https://cdn.jsdelivr.net https://fonts.googleapis.com; font-src 'self' https://fonts.gstatic.com; img-src 'self' data:; connect-src 'self' *;"
            
            # Custom Morgan logging
            log_message = f"{request.method} {request.path} {response.status_code} {response.content_length or 0} - {duration * 1000:.2f} ms"
            morgan_logger.info(log_message)
            
        return response

    # Global Exception Handler
    # [P1-8] Never leak str(e) (SQL text, schema, paths) to the client. Log the
    # full traceback server-side under a request id; return only the id.
    @app.errorhandler(Exception)
    def handle_exception(e):
        from werkzeug.exceptions import HTTPException
        if isinstance(e, HTTPException):
            return e

        import traceback
        request_id = str(uuid.uuid4())
        morgan_logger.error(
            f"Unhandled exception [request_id={request_id}] "
            f"{request.method} {request.path}: {e}\n{traceback.format_exc()}"
        )
        return jsonify({
            'error': 'Internal server error',
            'request_id': request_id
        }), 500

    # ── Loader.io domain verification ──────────────────────────────────────────
    @app.route('/loaderio-<token>.txt')
    @app.route('/loaderio-<token>.html')
    @app.route('/loaderio-<token>/')
    @app.route('/loaderio-<token>')
    @limiter.exempt
    def loaderio_verify(token):
        return f'loaderio-{token}', 200, {'Content-Type': 'text/plain'}

    # ── Health Check Endpoint ──────────────────────────────────────────────────
    @app.route('/health')
    @limiter.exempt
    def health():
        uptime = time.time() - metrics.start_time
        return jsonify({
            'status': 'ok',
            'uptime': f"{uptime:.1f}s"
        }), 200

    # ── OpenAPI Spec Endpoint ──────────────────────────────────────────────────
    @app.route('/openapi.json')
    @limiter.exempt
    def openapi_spec():
        return app.send_static_file('openapi.json')


    # ── Metrics Monitoring Endpoint ────────────────────────────────────────────
    @app.route('/metrics')
    @limiter.exempt
    def metrics_endpoint():
        uptime = time.time() - metrics.start_time
        avg_time = (metrics.total_response_time / metrics.total_requests * 1000) if metrics.total_requests > 0 else 0.0
        
        # Get memory usage in MB
        try:
            process = psutil.Process()
            memory_info = process.memory_info()
            memory_mb = memory_info.rss / (1024 * 1024)
        except Exception:
            memory_mb = 0.0
            
        return jsonify({
            'total_requests': metrics.total_requests,
            'avg_response_time_ms': round(avg_time, 2),
            'active_connections': metrics.active_connections,
            'uptime_seconds': int(uptime),
            'memory_usage_mb': round(memory_mb, 2)
        }), 200

    # [P0-1] Demo endpoints (db.drop_all!) are registered ONLY when
    # APP_ENV=demo. In every other environment the routes do not exist and the
    # paths fall through to Flask's default 404, so they are not advertised.
    # Even in demo, every request must carry X-Demo-Token matching DEMO_TOKEN;
    # a missing/wrong token also yields 404, never 403.
    if os.environ.get('APP_ENV') == 'demo':

        def _demo_token_valid():
            expected = os.environ.get('DEMO_TOKEN', '')
            supplied = request.headers.get('X-Demo-Token', '')
            return bool(expected) and hmac.compare_digest(supplied, expected)

        @app.route('/api/demo/reset', methods=['POST'])
        def demo_reset():
            if not _demo_token_valid():
                abort(404)
            db.drop_all()
            db.create_all()
            seed_data()
            return jsonify({'success': True, 'message': 'Demo data reset successfully'})

        @app.route('/api/demo/simulate_sale', methods=['POST'])
        def simulate_sale():
            if not _demo_token_valid():
                abort(404)
            from models import Product, Customer, Sale, SaleItem
            import random
            from datetime import timedelta

            customer_ids = [c.id for c in Customer.query.all()]
            product_list = Product.query.filter_by(active=True).all()
            if not product_list:
                return jsonify({'success': False, 'error': 'No active products found'}), 400

            num_items = random.randint(1, 3)
            items = random.sample(product_list, min(num_items, len(product_list)))
            total_cgst = 0; total_sgst = 0; grand_total = 0; sub = 0

            sale = Sale(
                customer_id=random.choice(customer_ids) if customer_ids else None,
                payment_method=random.choice(['cash', 'card', 'upi']),
                created_at=datetime.utcnow(),
                discount=0
            )
            db.session.add(sale)
            db.session.flush()

            for prod in items:
                qty = random.randint(1, 2)
                subtotal = round(prod.price * qty, 2)
                gst_amt = round(subtotal - subtotal / (1 + prod.gst_rate / 100), 2)
                total_cgst += gst_amt / 2
                total_sgst += gst_amt / 2
                grand_total += subtotal
                sub += round(subtotal - gst_amt, 2)

                si = SaleItem(
                    sale_id=sale.id,
                    product_id=prod.id,
                    quantity=qty,
                    price=prod.price,
                    gst_rate=prod.gst_rate,
                    subtotal=subtotal
                )
                db.session.add(si)
                prod.stock = max(0, prod.stock - qty)

            sale.subtotal = round(sub, 2)
            sale.total_cgst = round(total_cgst, 2)
            sale.total_sgst = round(total_sgst, 2)
            sale.total = round(grand_total, 2)
            sale.amount_paid = grand_total
            sale.change_given = 0

            db.session.commit()
            return jsonify({'success': True, 'sale': sale.to_dict()})

    with app.app_context():
        # [P0-6] Tests own their schema/seed lifecycle; auto-create/seed only
        # outside TESTING so the suite never inherits demo data.
        if not app.config.get('TESTING'):
            db.create_all()
            seed_data()
            try:
                from models import Category
                cat_updates = {
                    'உணவு': 'Food & Beverages',
                    'மளிகை': 'Grocery',
                    'மின்னணு': 'Electronics',
                    'ஆடை': 'Clothing & Textiles',
                    'உடல்நலம்': 'Health & Medicine',
                    'ஸ்டேஷனரி': 'Stationery'
                }
                updated = False
                for c in Category.query.all():
                    for k, v in cat_updates.items():
                        if k in c.name:
                            c.name = v
                            updated = True
                if updated:
                    db.session.commit()
            except Exception as e:
                morgan_logger.warning(f"Category migration check: {e}")

    return app


def seed_data():
    from models import Category, Product, Customer, Sale, SaleItem
    if Category.query.count() == 0:
        # Tamil Nadu relevant categories
        categories = [
            Category(name='Food & Beverages',   icon='🍛'),   # Food & Beverages
            Category(name='Grocery',            icon='🧺'),   # Grocery
            Category(name='Electronics',        icon='📱'),   # Electronics
            Category(name='Clothing & Textiles',icon='👗'),   # Clothing & Textiles
            Category(name='Health & Medicine',  icon='💊'),   # Health & Medicine
            Category(name='Stationery',         icon='📝'),   # Stationery
        ]
        db.session.add_all(categories)
        db.session.commit()

        # Tamil Nadu products with GST rates and HSN codes
        products = [
            # Food & Beverages (cat 1) — 5% GST on restaurant food
            Product(name='Filter Coffee',        sku='FB001', price=25.00,  cost=8.00,  stock=200, category_id=1, gst_rate=5,  hsn_code='0901', barcode='8901234560001'),
            Product(name='Masala Tea',           sku='FB002', price=20.00,  cost=5.00,  stock=200, category_id=1, gst_rate=5,  hsn_code='0902', barcode='8901234560002'),
            Product(name='Idli (2 pcs)',         sku='FB003', price=30.00,  cost=8.00,  stock=100, category_id=1, gst_rate=5,  hsn_code='1901', barcode='8901234560003'),
            Product(name='Dosa (Plain)',         sku='FB004', price=40.00,  cost=10.00, stock=100, category_id=1, gst_rate=5,  hsn_code='1901', barcode='8901234560004'),
            Product(name='Sambar Vada',          sku='FB005', price=35.00,  cost=10.00, stock=80,  category_id=1, gst_rate=5,  hsn_code='2106', barcode='8901234560005'),
            Product(name='Meals (Full)',         sku='FB006', price=120.00, cost=40.00, stock=50,  category_id=1, gst_rate=5,  hsn_code='2106', barcode='8901234560006'),
            Product(name='Parotta (2 pcs)',      sku='FB007', price=30.00,  cost=8.00,  stock=100, category_id=1, gst_rate=5,  hsn_code='1905', barcode='8901234560007'),
            Product(name='Biriyani',             sku='FB008', price=180.00, cost=60.00, stock=40,  category_id=1, gst_rate=5,  hsn_code='1901', barcode='8901234560008'),

            # Grocery (cat 2) — most staples 0% or 5% GST
            Product(name='Ponni Rice 5kg',       sku='GR001', price=220.00, cost=160.00, stock=50,  category_id=2, gst_rate=0,  hsn_code='1006', barcode='8901234560009'),
            Product(name='Toor Dal 1kg',         sku='GR002', price=130.00, cost=95.00,  stock=60,  category_id=2, gst_rate=0,  hsn_code='0713', barcode='8901234560010'),
            Product(name='Coconut Oil 1L',       sku='GR003', price=185.00, cost=130.00, stock=40,  category_id=2, gst_rate=5,  hsn_code='1513', barcode='8901234560011'),
            Product(name='Atta 5kg',             sku='GR004', price=260.00, cost=190.00, stock=30,  category_id=2, gst_rate=0,  hsn_code='1101', barcode='8901234560012'),
            Product(name='Idli Rice 5kg',        sku='GR005', price=210.00, cost=155.00, stock=45,  category_id=2, gst_rate=0,  hsn_code='1006', barcode='8901234560013'),
            Product(name='Tamarind 500g',        sku='GR006', price=55.00,  cost=38.00,  stock=80,  category_id=2, gst_rate=0,  hsn_code='0813', barcode='8901234560014'),

            # Electronics (cat 3) — 18% GST
            Product(name='USB-C Cable 1m',       sku='EL001', price=299.00,  cost=100.00, stock=60, category_id=3, gst_rate=18, hsn_code='8544', barcode='8901234560015'),
            Product(name='Phone Cover',          sku='EL002', price=199.00,  cost=60.00,  stock=80, category_id=3, gst_rate=18, hsn_code='3926', barcode='8901234560016'),
            Product(name='Earphones (Wired)',    sku='EL003', price=499.00,  cost=150.00, stock=35, category_id=3, gst_rate=18, hsn_code='8518', barcode='8901234560017'),
            Product(name='Screen Guard',         sku='EL004', price=149.00,  cost=40.00,  stock=100,category_id=3, gst_rate=18, hsn_code='3919', barcode='8901234560018'),
            Product(name='Power Bank 10000mAh', sku='EL005', price=999.00,  cost=450.00, stock=20, category_id=3, gst_rate=18, hsn_code='8507', barcode='8901234560019'),

            # Clothing (cat 4) — 5% GST under ₹1000, 12% above
            Product(name='Cotton Veshti',        sku='CL001', price=350.00,  cost=180.00, stock=30, category_id=4, gst_rate=5,  hsn_code='5208', barcode='8901234560020'),
            Product(name='Half Saree Set',       sku='CL002', price=850.00,  cost=450.00, stock=15, category_id=4, gst_rate=5,  hsn_code='5208', barcode='8901234560021'),
            Product(name='Cotton Shirt',         sku='CL003', price=550.00,  cost=260.00, stock=40, category_id=4, gst_rate=5,  hsn_code='6205', barcode='8901234560022'),
            Product(name='Salwar Set',           sku='CL004', price=1200.00, cost=650.00, stock=20, category_id=4, gst_rate=12, hsn_code='6211', barcode='8901234560023'),

            # Health (cat 5) — 12% GST
            Product(name='Paracetamol 500mg',   sku='HB001', price=25.00,  cost=12.00, stock=200, category_id=5, gst_rate=12, hsn_code='3004', barcode='8901234560024'),
            Product(name='Coconut Hair Oil',    sku='HB002', price=120.00, cost=65.00, stock=60,  category_id=5, gst_rate=18, hsn_code='3305', barcode='8901234560025'),
            Product(name='Neem Soap',           sku='HB003', price=45.00,  cost=22.00, stock=150, category_id=5, gst_rate=18, hsn_code='3401', barcode='8901234560026'),
            Product(name='Turmeric Powder',     sku='HB004', price=60.00,  cost=35.00, stock=100, category_id=5, gst_rate=5,  hsn_code='0910', barcode='8901234560027'),

            # Stationery (cat 6) — 12% GST
            Product(name='Ruled Notebook A4',   sku='ST001', price=60.00,  cost=32.00, stock=300, category_id=6, gst_rate=12, hsn_code='4820', barcode='8901234560028'),
            Product(name='Ball Pen Set (5)',    sku='ST002', price=40.00,  cost=18.00, stock=400, category_id=6, gst_rate=12, hsn_code='9608', barcode='8901234560029'),
            Product(name='Geometry Box',        sku='ST003', price=120.00, cost=65.00, stock=80,  category_id=6, gst_rate=12, hsn_code='9017', barcode='8901234560030'),
        ]
        db.session.add_all(products)

        # Tamil Nadu customer names
        customers = [
            Customer(name='Walk-in Customer',          phone='0000000000'),
            Customer(name='Murugan Rajan',             email='murugan@gmail.com', phone='9841001001'),
            Customer(name='Kavitha Suresh',            email='kavitha@gmail.com', phone='9841002002'),
            Customer(name='Selvam Krishnamurthy',      email='selvam@gmail.com',  phone='9841003003'),
            Customer(name='Priya Devi',                email='priya@gmail.com',   phone='9841004004'),
            Customer(name='Annamalai Textiles',        email='textiles@gmail.com',phone='9841005005', gstin='33AABCA1234B1ZX'),
        ]
        db.session.add_all(customers)
        db.session.commit()

        # Seed sample sales with GST
        import random
        from datetime import timedelta
        customer_ids = [c.id for c in Customer.query.all()]
        product_list = Product.query.all()
        for i in range(30):
            days_ago = random.randint(0, 30)
            sale_date = datetime.utcnow() - timedelta(days=days_ago, hours=random.randint(0, 10))
            num_items = random.randint(1, 5)
            items = random.sample(product_list, min(num_items, len(product_list)))
            total_cgst = 0; total_sgst = 0; grand_total = 0; sub = 0
            sale = Sale(
                customer_id=random.choice(customer_ids),
                payment_method=random.choice(['cash', 'card', 'upi', 'upi']),
                created_at=sale_date,
                discount=0
            )
            db.session.add(sale)
            db.session.flush()
            for prod in items:
                qty = random.randint(1, 3)
                subtotal = round(prod.price * qty, 2)
                gst_amt = round(subtotal - subtotal / (1 + prod.gst_rate / 100), 2)
                total_cgst += gst_amt / 2
                total_sgst += gst_amt / 2
                grand_total += subtotal
                sub += round(subtotal - gst_amt, 2)
                si = SaleItem(sale_id=sale.id, product_id=prod.id, quantity=qty,
                              price=prod.price, gst_rate=prod.gst_rate, subtotal=subtotal)
                db.session.add(si)
            sale.subtotal = round(sub, 2)
            sale.total_cgst = round(total_cgst, 2)
            sale.total_sgst = round(total_sgst, 2)
            sale.total = round(grand_total, 2)
            sale.amount_paid = grand_total + random.uniform(0, 50)
            sale.change_given = round(sale.amount_paid - grand_total, 2)
        db.session.commit()


app = create_app()

if __name__ == '__main__':
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port, debug=False)


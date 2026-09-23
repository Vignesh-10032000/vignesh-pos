from flask import Blueprint, render_template, jsonify, redirect, request, session
from models import Sale, Product, Customer, SaleItem
from extensions import db, cache
from datetime import datetime, timedelta
from sqlalchemy import func

dashboard_bp = Blueprint('dashboard', __name__)

VALID_OPERATORS = {
    'admin': {'password': 'admin123', 'role': 'Store Admin'},
    'cashier': {'password': 'pos123', 'role': 'Cashier'},
    'inventory': {'password': 'stock123', 'role': 'Inventory Staff'},
}

def authenticate_operator(username, password):
    if not username or not password:
        return None
    lower_user = str(username).strip().lower()
    pass_str = str(password).strip()
    if lower_user in VALID_OPERATORS:
        if VALID_OPERATORS[lower_user]['password'] == pass_str:
            return {
                'username': str(username).strip(),
                'role': VALID_OPERATORS[lower_user]['role']
            }
        return None
    if len(str(username).strip()) >= 2 and len(pass_str) >= 3:
        return {
            'username': str(username).strip(),
            'role': 'Store Operator'
        }
    return None

@dashboard_bp.route('/')
def index():
    if not session.get('user'):
        return redirect('/login')
    return render_template('dashboard.html', page='dashboard')

@dashboard_bp.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'GET':
        if session.get('user'):
            return redirect('/pos')
        return render_template('login.html', page='login')

    username = request.form.get('username', '').strip()
    password = request.form.get('password', '').strip()
    auth_user = authenticate_operator(username, password)
    if auth_user:
        session['user'] = auth_user
        session.permanent = True
        next_url = request.args.get('next') or '/pos'
        return redirect(next_url)

    return render_template('login.html', page='login', error='Invalid username or password')

@dashboard_bp.route('/api/login', methods=['POST'])
def api_login():
    data = request.get_json(silent=True) or request.form or {}
    username = str(data.get('username', '')).strip()
    password = str(data.get('password', '')).strip()

    auth_user = authenticate_operator(username, password)
    if not auth_user:
        return jsonify({
            'success': False,
            'message': 'Invalid credentials. Please check your username and passcode.'
        }), 401

    session['user'] = auth_user
    session.permanent = True
    next_url = request.args.get('next') or '/pos'

    return jsonify({
        'success': True,
        'user': auth_user,
        'redirect': next_url
    })

@dashboard_bp.route('/logout')
def logout():
    session.clear()
    resp = redirect('/login')
    resp.delete_cookie('session')
    return resp

@dashboard_bp.route('/settings')
def settings():
    if not session.get('user'):
        return redirect('/login?next=/settings')
    return render_template('settings.html', page='settings')

@dashboard_bp.route('/api/dashboard/stats')
@cache.cached(timeout=10)
def stats():
    today = datetime.utcnow().date()
    today_start = datetime.combine(today, datetime.min.time())
    week_start = today_start - timedelta(days=7)
    month_start = today_start - timedelta(days=30)

    today_sales = db.session.query(func.sum(Sale.total)).filter(Sale.created_at >= today_start, Sale.status == 'COMPLETED').scalar() or 0
    today_count = Sale.query.filter(Sale.created_at >= today_start, Sale.status == 'COMPLETED').count()
    week_sales = db.session.query(func.sum(Sale.total)).filter(Sale.created_at >= week_start, Sale.status == 'COMPLETED').scalar() or 0
    month_sales = db.session.query(func.sum(Sale.total)).filter(Sale.created_at >= month_start, Sale.status == 'COMPLETED').scalar() or 0
    total_products = Product.query.filter_by(active=True).count()
    low_stock = Product.query.filter(Product.stock <= Product.low_stock_threshold, Product.active==True).count()
    total_customers = Customer.query.count()

    # Daily revenue for last 7 days
    daily_data = []
    for i in range(6, -1, -1):
        day = today_start - timedelta(days=i)
        day_end = day + timedelta(days=1)
        revenue = db.session.query(func.sum(Sale.total)).filter(
            Sale.created_at >= day, Sale.created_at < day_end, Sale.status == 'COMPLETED'
        ).scalar() or 0
        daily_data.append({'date': day.strftime('%a'), 'revenue': round(revenue, 2)})

    # Top products
    top_products = db.session.query(
        Product.name,
        func.sum(SaleItem.quantity).label('qty'),
        func.sum(SaleItem.subtotal).label('revenue')
    ).join(SaleItem, Product.id == SaleItem.product_id)\
     .join(Sale, Sale.id == SaleItem.sale_id)\
     .filter(Sale.created_at >= month_start, Sale.status == 'COMPLETED')\
     .group_by(Product.id)\
     .order_by(func.sum(SaleItem.subtotal).desc())\
     .limit(5).all()

    # Recent sales
    recent_sales = Sale.query.filter(Sale.cancelled_at.is_(None)).order_by(Sale.created_at.desc()).limit(8).all()

    # Low stock products
    low_stock_products = Product.query.filter(
        Product.stock <= Product.low_stock_threshold, Product.active==True
    ).order_by(Product.stock.asc()).limit(5).all()

    return jsonify({
        'today_sales': round(today_sales, 2),
        'today_count': today_count,
        'today_cgst': round(db.session.query(func.sum(Sale.total_cgst)).filter(Sale.created_at >= today_start, Sale.status == 'COMPLETED').scalar() or 0, 2),
        'today_sgst': round(db.session.query(func.sum(Sale.total_sgst)).filter(Sale.created_at >= today_start, Sale.status == 'COMPLETED').scalar() or 0, 2),
        'week_sales': round(week_sales, 2),
        'month_sales': round(month_sales, 2),
        'total_products': total_products,
        'low_stock': low_stock,
        'total_customers': total_customers,
        'daily_data': daily_data,
        'top_products': [{'name': p.name, 'qty': int(p.qty), 'revenue': round(float(p.revenue), 2)} for p in top_products],
        'recent_sales': [s.to_dict() for s in recent_sales],
        'low_stock_products': [p.to_dict() for p in low_stock_products],
    })


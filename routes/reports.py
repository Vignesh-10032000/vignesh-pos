from flask import Blueprint, render_template, request, jsonify
from models import Sale, SaleItem, Product, Category, Customer
from extensions import db
from datetime import datetime, timedelta
from sqlalchemy import func

reports_bp = Blueprint('reports', __name__)

@reports_bp.route('/reports')
def reports():
    return render_template('reports.html', page='reports')

@reports_bp.route('/api/reports/revenue')
def revenue_report():
    period = request.args.get('period', '30')  # days
    days = int(period)
    start = datetime.utcnow() - timedelta(days=days)

    data = []
    today = datetime.utcnow().date()
    for i in range(days - 1, -1, -1):
        day = today - timedelta(days=i)
        day_start = datetime.combine(day, datetime.min.time())
        day_end = day_start + timedelta(days=1)
        revenue = db.session.query(func.sum(Sale.total)).filter(
            Sale.created_at >= day_start, Sale.created_at < day_end, Sale.status == 'COMPLETED'
        ).scalar() or 0
        count = Sale.query.filter(Sale.created_at >= day_start, Sale.created_at < day_end, Sale.status == 'COMPLETED').count()
        data.append({'date': day.strftime('%b %d'), 'revenue': round(revenue, 2), 'count': count})

    total = sum(d['revenue'] for d in data)
    total_orders = sum(d['count'] for d in data)
    avg_order = total / total_orders if total_orders > 0 else 0
    total_cgst = db.session.query(func.sum(Sale.total_cgst)).filter(Sale.created_at >= start, Sale.status == 'COMPLETED').scalar() or 0
    total_sgst = db.session.query(func.sum(Sale.total_sgst)).filter(Sale.created_at >= start, Sale.status == 'COMPLETED').scalar() or 0

    return jsonify({
        'data': data,
        'total': round(total, 2),
        'total_orders': total_orders,
        'avg_order': round(avg_order, 2),
        'total_cgst': round(float(total_cgst), 2),
        'total_sgst': round(float(total_sgst), 2),
    })


@reports_bp.route('/api/reports/top-products')
def top_products():
    period = request.args.get('period', '30')
    days = int(period)
    start = datetime.utcnow() - timedelta(days=days)
    results = db.session.query(
        Product.name,
        Product.id,
        func.sum(SaleItem.quantity).label('qty'),
        func.sum(SaleItem.subtotal).label('revenue'),
    ).join(SaleItem, Product.id == SaleItem.product_id)\
     .join(Sale, Sale.id == SaleItem.sale_id)\
     .filter(Sale.created_at >= start, Sale.status == 'COMPLETED')\
     .group_by(Product.id)\
     .order_by(func.sum(SaleItem.subtotal).desc())\
     .limit(10).all()
    return jsonify([
        {'name': r.name, 'qty': int(r.qty), 'revenue': round(float(r.revenue), 2)}
        for r in results
    ])

@reports_bp.route('/api/reports/payment-methods')
def payment_methods():
    period = request.args.get('period', '30')
    days = int(period)
    start = datetime.utcnow() - timedelta(days=days)
    results = db.session.query(
        Sale.payment_method,
        func.count(Sale.id).label('count'),
        func.sum(Sale.total).label('total')
    ).filter(Sale.created_at >= start, Sale.status == 'COMPLETED')\
     .group_by(Sale.payment_method).all()
    return jsonify([
        {'method': r.payment_method, 'count': r.count, 'total': round(float(r.total or 0), 2)}
        for r in results
    ])

@reports_bp.route('/api/reports/categories')
def category_revenue():
    period = request.args.get('period', '30')
    days = int(period)
    start = datetime.utcnow() - timedelta(days=days)
    results = db.session.query(
        Category.name,
        func.sum(SaleItem.subtotal).label('revenue'),
    ).join(Product, Category.id == Product.category_id)\
     .join(SaleItem, Product.id == SaleItem.product_id)\
     .join(Sale, Sale.id == SaleItem.sale_id)\
     .filter(Sale.created_at >= start, Sale.status == 'COMPLETED')\
     .group_by(Category.id)\
     .order_by(func.sum(SaleItem.subtotal).desc()).all()
    return jsonify([
        {'name': r.name, 'revenue': round(float(r.revenue or 0), 2)}
        for r in results
    ])

@reports_bp.route('/api/reports/customers')
def best_customers():
    period = request.args.get('period', '30')
    days = int(period)
    start = datetime.utcnow() - timedelta(days=days)
    
    results = db.session.query(
        Customer.name,
        Customer.phone,
        func.count(Sale.id).label('orders'),
        func.sum(Sale.total).label('total_spent'),
        Customer.points
    ).join(Sale, Customer.id == Sale.customer_id)\
     .filter(Sale.created_at >= start, Sale.status == 'COMPLETED')\
     .group_by(Customer.id)\
     .order_by(func.sum(Sale.total).desc())\
     .limit(10).all()
     
    return jsonify([
        {
            'name': r.name,
            'phone': r.phone or '—',
            'orders': int(r.orders),
            'total_spent': round(float(r.total_spent or 0), 2),
            'points': int(r.points)
        }
        for r in results
    ])

@reports_bp.route('/api/reports/performance')
def product_performance():
    period = request.args.get('period', '30')
    days = int(period)
    start = datetime.utcnow() - timedelta(days=days)
    
    fast_moving = db.session.query(
        Product.name,
        Product.sku,
        func.sum(SaleItem.quantity).label('qty_sold')
    ).join(SaleItem, Product.id == SaleItem.product_id)\
     .join(Sale, Sale.id == SaleItem.sale_id)\
     .filter(Sale.created_at >= start, Sale.status == 'COMPLETED')\
     .group_by(Product.id)\
     .order_by(func.sum(SaleItem.quantity).desc())\
     .limit(5).all()

    sold_product_ids = db.session.query(SaleItem.product_id)\
                                 .join(Sale, Sale.id == SaleItem.sale_id)\
                                 .filter(Sale.created_at >= start, Sale.status == 'COMPLETED').distinct().all()
    sold_ids = [r[0] for r in sold_product_ids]
    
    slow_moving = Product.query.filter(
        Product.active == True,
        Product.stock > 0,
        ~Product.id.in_(sold_ids) if sold_ids else True
    ).order_by(Product.stock.desc()).limit(5).all()

    return jsonify({
        'fast_moving': [{'name': r.name, 'sku': r.sku, 'qty': int(r.qty_sold)} for r in fast_moving],
        'slow_moving': [{'name': p.name, 'sku': p.sku, 'stock': p.stock} for p in slow_moving]
    })

@reports_bp.route('/api/reports/day-close', methods=['GET'])
def day_close():
    from models import Sale
    from datetime import datetime
    
    today = datetime.utcnow().date()
    start = datetime.combine(today, datetime.min.time())
    end = datetime.combine(today, datetime.max.time())
    
    sales_today = Sale.query.filter(
        Sale.created_at >= start,
        Sale.created_at <= end, Sale.status == 'COMPLETED'
    ).all()
    
    total_cash = sum(s.total for s in sales_today if s.payment_method == 'cash')
    total_upi = sum(s.total for s in sales_today if s.payment_method in ('upi', 'mobile'))
    total_card = sum(s.total for s in sales_today if s.payment_method == 'card')
    total_cgst = sum(s.total_cgst for s in sales_today)
    total_sgst = sum(s.total_sgst for s in sales_today)
    grand_total = sum(s.total for s in sales_today)
    
    return jsonify({
        'success': True,
        'date': today.strftime('%d-%m-%Y'),
        'total_transactions': len(sales_today),
        'cash': round(total_cash, 2),
        'upi': round(total_upi, 2),
        'card': round(total_card, 2),
        'total_cgst': round(total_cgst, 2),
        'total_sgst': round(total_sgst, 2),
        'grand_total': round(grand_total, 2)
    })

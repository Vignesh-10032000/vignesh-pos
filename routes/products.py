from flask import Blueprint, render_template, request, jsonify
from models import Product, Category
from extensions import db

products_bp = Blueprint('products', __name__)

@products_bp.route('/products')
def products():
    return render_template('products.html', page='products')

@products_bp.route('/api/products')
def get_products():
    q = request.args.get('q', '')
    category_id = request.args.get('category_id', type=int)
    query = Product.query
    if q:
        query = query.filter(
            (Product.name.ilike(f'%{q}%')) |
            (Product.sku.ilike(f'%{q}%')) |
            (Product.hsn_code.ilike(f'%{q}%'))
        )
    if category_id:
        query = query.filter_by(category_id=category_id)
    products = query.order_by(Product.name).all()
    return jsonify([p.to_dict() for p in products])

@products_bp.route('/api/products/<int:pid>', methods=['GET'])
def get_product(pid):
    p = Product.query.get_or_404(pid)
    return jsonify(p.to_dict())

@products_bp.route('/api/products', methods=['POST'])
def create_product():
    data = request.get_json()
    if Product.query.filter_by(sku=data.get('sku')).first():
        return jsonify({'error': 'SKU already exists'}), 400
    p = Product(
        name=data['name'],
        sku=data['sku'],
        barcode=data.get('barcode'),
        price=float(data['price']),
        cost=float(data.get('cost', 0)),
        stock=int(data.get('stock', 0)),
        low_stock_threshold=int(data.get('low_stock_threshold', 10)),
        category_id=data.get('category_id'),
        gst_rate=float(data.get('gst_rate', 0)),
        hsn_code=data.get('hsn_code', ''),
        is_veg=data.get('is_veg', True),
        active=data.get('active', True),
    )
    db.session.add(p)
    db.session.commit()
    return jsonify(p.to_dict()), 201

@products_bp.route('/api/products/<int:pid>', methods=['PUT'])
def update_product(pid):
    p = Product.query.get_or_404(pid)
    data = request.get_json()
    p.name = data.get('name', p.name)
    p.sku = data.get('sku', p.sku)
    p.barcode = data.get('barcode', p.barcode)
    p.price = float(data.get('price', p.price))
    p.cost = float(data.get('cost', p.cost))
    p.stock = int(data.get('stock', p.stock))
    p.low_stock_threshold = int(data.get('low_stock_threshold', p.low_stock_threshold))
    p.category_id = data.get('category_id', p.category_id)
    p.gst_rate = float(data.get('gst_rate', p.gst_rate))
    p.hsn_code = data.get('hsn_code', p.hsn_code)
    p.is_veg = data.get('is_veg', p.is_veg)
    p.active = data.get('active', p.active)
    db.session.commit()
    return jsonify(p.to_dict())

@products_bp.route('/api/products/<int:pid>', methods=['DELETE'])
def delete_product(pid):
    p = Product.query.get_or_404(pid)
    p.active = False
    db.session.commit()
    return jsonify({'success': True})

@products_bp.route('/api/products/<int:pid>/stock', methods=['PATCH'])
def adjust_stock(pid):
    p = Product.query.get_or_404(pid)
    data = request.get_json()
    adjustment = int(data.get('adjustment', 0))  # positive=add, negative=remove
    p.stock = max(0, p.stock + adjustment)
    db.session.commit()
    return jsonify({'success': True, 'new_stock': p.stock})

@products_bp.route('/api/categories')
def get_categories():
    cats = Category.query.all()
    return jsonify([c.to_dict() for c in cats])

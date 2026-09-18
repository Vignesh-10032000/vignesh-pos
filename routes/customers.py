from flask import Blueprint, render_template, request, jsonify
from models import Customer
from extensions import db

customers_bp = Blueprint('customers', __name__)

@customers_bp.route('/customers')
def customers():
    return render_template('customers.html', page='customers')

@customers_bp.route('/api/customers')
def get_customers():
    q = request.args.get('q', '')
    query = Customer.query
    if q:
        query = query.filter(
            (Customer.name.ilike(f'%{q}%')) |
            (Customer.email.ilike(f'%{q}%')) |
            (Customer.phone.ilike(f'%{q}%'))
        )
    customers = query.order_by(Customer.name).all()
    return jsonify([c.to_dict() for c in customers])

@customers_bp.route('/api/customers/<int:cid>', methods=['GET'])
def get_customer(cid):
    c = Customer.query.get_or_404(cid)
    return jsonify(c.to_dict())

@customers_bp.route('/api/customers', methods=['POST'])
def create_customer():
    data = request.get_json()
    c = Customer(
        name=data['name'],
        email=data.get('email', ''),
        phone=data.get('phone', ''),
        gstin=data.get('gstin', ''),
    )
    db.session.add(c)
    db.session.commit()
    return jsonify(c.to_dict()), 201

@customers_bp.route('/api/customers/<int:cid>', methods=['PUT'])
def update_customer(cid):
    c = Customer.query.get_or_404(cid)
    data = request.get_json()
    c.name = data.get('name', c.name)
    c.email = data.get('email', c.email)
    c.phone = data.get('phone', c.phone)
    c.gstin = data.get('gstin', c.gstin)
    db.session.commit()
    return jsonify(c.to_dict())

@customers_bp.route('/api/customers/<int:cid>', methods=['DELETE'])
def delete_customer(cid):
    c = Customer.query.get_or_404(cid)
    db.session.delete(c)
    db.session.commit()
    return jsonify({'success': True})

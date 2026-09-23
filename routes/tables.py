from flask import Blueprint, request, jsonify
from datetime import datetime
from decimal import Decimal, ROUND_HALF_UP, ROUND_FLOOR

from extensions import db
from models import DiningTable, Sale, SaleItem, Product, Customer, KOT, KOTItem
from routes.pos import _parse_money, _apportion_discount, TWO_PLACES
from flask import render_template

tables_bp = Blueprint('tables', __name__)

@tables_bp.route('/tables')
def tables_page():
    return render_template('tables.html', page='tables')

@tables_bp.route('/kot/<int:id>/print')
def print_kot_page(id):
    kot = KOT.query.get_or_404(id)
    return render_template('kot.html', kot=kot)

@tables_bp.route('/api/tables', methods=['GET'])
def get_tables():
    tables = DiningTable.query.all()
    result = []
    for t in tables:
        t_dict = t.to_dict()
        if t.current_order_id:
            sale = Sale.query.get(t.current_order_id)
            if sale:
                t_dict['current_order_total'] = sum(i.subtotal for i in sale.items)
                t_dict['item_count'] = sum(i.quantity for i in sale.items)
        result.append(t_dict)
    return jsonify(result)

@tables_bp.route('/api/tables/<int:id>/open', methods=['POST'])
def open_table(id):
    table = DiningTable.query.get_or_404(id)
    if table.status != 'AVAILABLE' and table.status != 'RESERVED':
        return jsonify({'error': 'Table is not available'}), 400
    
    data = request.get_json(silent=True) or {}
    waiter_name = data.get('waiter_name')

    sale = Sale(status='OPEN', order_type='DINE_IN', table_id=table.id, waiter_name=waiter_name)
    db.session.add(sale)
    db.session.flush()

    table.status = 'OCCUPIED'
    table.occupied_at = datetime.utcnow()
    table.current_order_id = sale.id

    db.session.commit()
    return jsonify({'success': True, 'sale': sale.to_dict()})

@tables_bp.route('/api/tables/<int:id>/items', methods=['POST'])
def add_items(id):
    table = DiningTable.query.get_or_404(id)
    if not table.current_order_id:
        return jsonify({'error': 'Table has no open order'}), 400

    sale = Sale.query.get(table.current_order_id)
    if not sale:
        return jsonify({'error': 'Sale not found'}), 404

    data = request.get_json(silent=True)
    if not isinstance(data, dict) or not data.get('items'):
        return jsonify({'error': 'Invalid items'}), 400

    for item in data['items']:
        product = Product.query.get(item['product_id'])
        if not product:
            return jsonify({'error': f'Product {item["product_id"]} not found'}), 404
        
        qty = item['quantity']
        if qty <= 0:
            return jsonify({'error': 'Quantity must be > 0'}), 400
        
        gross = (Decimal(str(product.price)) * qty).quantize(TWO_PLACES, rounding=ROUND_HALF_UP)
        
        si = SaleItem(
            sale_id=sale.id,
            product_id=product.id,
            quantity=qty,
            price=product.price,
            gst_rate=product.gst_rate,
            subtotal=float(gross),
            cooking_notes=item.get('cooking_notes')
        )
        db.session.add(si)

    db.session.commit()
    return jsonify({'success': True, 'sale': sale.to_dict()})

@tables_bp.route('/api/tables/<int:id>/items/<int:item_id>', methods=['DELETE'])
def remove_item(id, item_id):
    table = DiningTable.query.get_or_404(id)
    if not table.current_order_id:
        return jsonify({'error': 'Table has no open order'}), 400

    item = SaleItem.query.get_or_404(item_id)
    if item.sale_id != table.current_order_id:
        return jsonify({'error': 'Item does not belong to this order'}), 400

    # Calculate how many of this product have been sent to the kitchen
    kots = KOT.query.filter_by(order_id=table.current_order_id).all()
    kot_ids = [k.id for k in kots]
    kot_items = KOTItem.query.filter(KOTItem.kot_id.in_(kot_ids), KOTItem.product_id == item.product_id).all()
    kot_qty = sum(ki.quantity for ki in kot_items)
    
    # Calculate total quantity of this product currently in the order
    sale = Sale.query.get(table.current_order_id)
    product_qty_in_sale = sum(si.quantity for si in sale.items if si.product_id == item.product_id)
    
    # If removing this item would drop the total quantity below what was already sent, it's invalid
    if (product_qty_in_sale - item.quantity) < kot_qty:
        return jsonify({'error': 'Item already sent to kitchen'}), 400

    db.session.delete(item)
    db.session.commit()
    return jsonify({'success': True})

@tables_bp.route('/api/tables/<int:id>/kot', methods=['POST'])
def fire_kot(id):
    table = DiningTable.query.get_or_404(id)
    if not table.current_order_id:
        return jsonify({'error': 'Table has no open order'}), 400
    
    sale = Sale.query.get(table.current_order_id)
    
    # Calculate what has been sent already
    kots = KOT.query.filter_by(order_id=sale.id).all()
    kot_ids = [k.id for k in kots]
    
    # We will map product_id -> quantity sent
    sent_qtys = {}
    if kot_ids:
        all_kot_items = KOTItem.query.filter(KOTItem.kot_id.in_(kot_ids)).all()
        for ki in all_kot_items:
            sent_qtys[ki.product_id] = sent_qtys.get(ki.product_id, 0) + ki.quantity
            
    # Calculate what needs to be sent
    # Also handle cooking notes (which might not aggregate perfectly if different)
    # The requirement says "Find all SaleItems on this table's open order that are NOT yet on any KOTItem."
    # Since KOTItems don't have a sale_item_id FK, we just match by product_id and difference the total quantity.
    
    new_items_to_send = []
    # To properly carry over cooking notes, we will iterate sale items and deduct sent quantities
    for si in sale.items:
        sent = sent_qtys.get(si.product_id, 0)
        
        if sent >= si.quantity:
            sent_qtys[si.product_id] -= si.quantity
            unsent = 0
        else:
            unsent = si.quantity - sent
            sent_qtys[si.product_id] = 0
            
        if unsent > 0:
            new_items_to_send.append({
                'product_id': si.product_id,
                'quantity': unsent,
                'cooking_notes': si.cooking_notes
            })
            
    if not new_items_to_send:
        return jsonify({'error': 'No new items to send'}), 400
        
    # Get max kot_number for today
    today = datetime.utcnow().date()
    today_start = datetime(today.year, today.month, today.day)
    last_kot = KOT.query.filter(KOT.created_at >= today_start).order_by(KOT.kot_number.desc()).first()
    next_kot_number = 1 if not last_kot else last_kot.kot_number + 1
    
    new_kot = KOT(
        kot_number=next_kot_number,
        order_id=sale.id,
        table_id=table.id,
        waiter_name=sale.waiter_name,
        status='SENT'
    )
    db.session.add(new_kot)
    db.session.flush()
    
    for item in new_items_to_send:
        ki = KOTItem(
            kot_id=new_kot.id,
            product_id=item['product_id'],
            quantity=item['quantity'],
            cooking_notes=item['cooking_notes']
        )
        db.session.add(ki)
        
    db.session.commit()
    return jsonify({'success': True, 'kot': new_kot.to_dict()})

@tables_bp.route('/api/tables/<int:id>/print-bill', methods=['POST'])
def print_bill(id):
    table = DiningTable.query.get_or_404(id)
    if not table.current_order_id:
        return jsonify({'error': 'Table has no open order'}), 400

    sale = Sale.query.get(table.current_order_id)
    if not sale.items:
        return jsonify({'error': 'No items in order'}), 400

    # Calculate GST totals
    line_gross = [Decimal(str(item.subtotal)).quantize(TWO_PLACES, rounding=ROUND_HALF_UP) for item in sale.items]
    grand_total = sum(line_gross, Decimal('0.00'))
    discount = Decimal(str(sale.discount)).quantize(TWO_PLACES, rounding=ROUND_HALF_UP)
    
    if discount > grand_total:
        discount = grand_total
        sale.discount = float(discount)
        
    discount_shares = _apportion_discount(discount, line_gross)
    
    sub_total = Decimal('0.00')
    total_cgst = Decimal('0.00')
    total_sgst = Decimal('0.00')
    
    for item, share in zip(sale.items, discount_shares):
        gross = Decimal(str(item.subtotal)).quantize(TWO_PLACES, rounding=ROUND_HALF_UP)
        net = gross - share
        rate = Decimal(str(item.gst_rate))
        taxable = (net / (Decimal('1') + rate / Decimal('100'))).quantize(TWO_PLACES, rounding=ROUND_HALF_UP)
        gst_amount = net - taxable
        cgst = (gst_amount / 2).quantize(TWO_PLACES, rounding=ROUND_HALF_UP)
        sgst = gst_amount - cgst
        sub_total += taxable
        total_cgst += cgst
        total_sgst += sgst

    total_after_discount = grand_total - discount
    
    sale.subtotal = float(sub_total)
    sale.total_cgst = float(total_cgst)
    sale.total_sgst = float(total_sgst)
    sale.total = float(total_after_discount)
    sale.status = 'BILLED'
    
    table.status = 'BILLED'
    db.session.commit()
    
    return jsonify({'success': True, 'sale': sale.to_dict()})

@tables_bp.route('/api/tables/<int:id>/settle', methods=['POST'])
def settle_table(id):
    table = DiningTable.query.get_or_404(id)
    if not table.current_order_id:
        return jsonify({'error': 'Table has no open order'}), 400

    sale = Sale.query.get(table.current_order_id)
    data = request.get_json(silent=True) or {}
    
    discount = _parse_money(data.get('discount', sale.discount)) or Decimal('0.00')
    sale.discount = float(discount)
    
    # Recalculate bill
    line_gross = [Decimal(str(item.subtotal)).quantize(TWO_PLACES, rounding=ROUND_HALF_UP) for item in sale.items]
    grand_total = sum(line_gross, Decimal('0.00'))
    
    if discount > grand_total:
        return jsonify({'error': 'discount must not exceed the cart total'}), 400
        
    discount_shares = _apportion_discount(discount, line_gross)
    
    sub_total = Decimal('0.00')
    total_cgst = Decimal('0.00')
    total_sgst = Decimal('0.00')
    
    for item, share in zip(sale.items, discount_shares):
        gross = Decimal(str(item.subtotal)).quantize(TWO_PLACES, rounding=ROUND_HALF_UP)
        net = gross - share
        rate = Decimal(str(item.gst_rate))
        taxable = (net / (Decimal('1') + rate / Decimal('100'))).quantize(TWO_PLACES, rounding=ROUND_HALF_UP)
        gst_amount = net - taxable
        cgst = (gst_amount / 2).quantize(TWO_PLACES, rounding=ROUND_HALF_UP)
        sgst = gst_amount - cgst
        sub_total += taxable
        total_cgst += cgst
        total_sgst += sgst

    total_after_discount = grand_total - discount
    
    amount_paid = _parse_money(data.get('amount_paid', total_after_discount))
    if amount_paid is None or amount_paid < Decimal('0.00'):
        return jsonify({'error': 'Invalid amount_paid'}), 400

    change = amount_paid - total_after_discount

    sale.subtotal = float(sub_total)
    sale.total_cgst = float(total_cgst)
    sale.total_sgst = float(total_sgst)
    sale.total = float(total_after_discount)
    sale.amount_paid = float(amount_paid)
    sale.change_given = float(max(Decimal('0.00'), change))
    sale.payment_method = data.get('payment_method', 'cash')
    sale.status = 'COMPLETED'
    
    # Decrease stock for items
    for item in sale.items:
        product = Product.query.get(item.product_id)
        if product:
            product.stock -= item.quantity
            
    customer_id = data.get('customer_id')
    if customer_id:
        customer = Customer.query.get(customer_id)
        if customer:
            sale.customer_id = customer.id
            customer.points += int(total_after_discount // 10)
            
    # Free table
    table.status = 'AVAILABLE'
    table.current_order_id = None
    table.occupied_at = None
    
    db.session.commit()
    return jsonify({'success': True, 'sale': sale.to_dict()})

@tables_bp.route('/api/tables/<int:id>/cancel', methods=['POST'])
def cancel_order(id):
    table = DiningTable.query.get_or_404(id)
    if not table.current_order_id:
        return jsonify({'error': 'Table has no open order'}), 400
        
    sale = Sale.query.get(table.current_order_id)
    data = request.get_json(silent=True) or {}
    reason = data.get('reason', '')
    
    sale.status = 'CANCELLED'
    sale.cancelled_at = datetime.utcnow()
    sale.notes = reason
    
    # Free table
    table.status = 'AVAILABLE'
    table.current_order_id = None
    table.occupied_at = None
    
    db.session.commit()
    return jsonify({'success': True})

@tables_bp.route('/api/tables/config', methods=['POST'])
def create_table():
    data = request.get_json(silent=True)
    if not data or not data.get('table_number'):
        return jsonify({'error': 'table_number is required'}), 400
        
    table = DiningTable(
        table_number=data['table_number'],
        area=data.get('area', 'Main Hall'),
        capacity=data.get('capacity', 4)
    )
    db.session.add(table)
    db.session.commit()
    return jsonify({'success': True, 'table': table.to_dict()})

@tables_bp.route('/api/tables/config/<int:id>', methods=['PUT'])
def update_table(id):
    table = DiningTable.query.get_or_404(id)
    data = request.get_json(silent=True)
    if not data:
        return jsonify({'error': 'Invalid data'}), 400
        
    if 'table_number' in data:
        table.table_number = data['table_number']
    if 'area' in data:
        table.area = data['area']
    if 'capacity' in data:
        table.capacity = data['capacity']
        
    db.session.commit()
    return jsonify({'success': True, 'table': table.to_dict()})

@tables_bp.route('/api/kot/pending', methods=['GET'])
def get_pending_kots():
    kots = KOT.query.filter(KOT.status.in_(['SENT', 'IN_PROGRESS'])).order_by(KOT.created_at).all()
    return jsonify([k.to_dict() for k in kots])

@tables_bp.route('/api/kot/<int:id>/status', methods=['PATCH'])
def update_kot_status(id):
    kot = KOT.query.get_or_404(id)
    data = request.get_json(silent=True)
    if not data or not data.get('status'):
        return jsonify({'error': 'status required'}), 400
        
    kot.status = data['status']
    db.session.commit()
    return jsonify({'success': True, 'kot': kot.to_dict()})

@tables_bp.route('/api/tables/<int:id>/order', methods=['GET'])
def get_table_order(id):
    table = DiningTable.query.get_or_404(id)
    if not table.current_order_id:
        return jsonify({'error': 'Table has no open order'}), 400
        
    sale = Sale.query.get(table.current_order_id)
    kots = KOT.query.filter_by(order_id=sale.id).all()
    
    result = sale.to_dict()
    result['kots'] = [k.to_dict() for k in kots]
    
    return jsonify(result)

import os

from flask import Blueprint, render_template, request, jsonify
from models import Sale, SaleItem, SaleReturn, SaleReturnItem, Product
from extensions import db
from datetime import datetime

sales_bp = Blueprint('sales', __name__)

@sales_bp.route('/sales')
def sales():
    return render_template('sales.html', page='sales')

@sales_bp.route('/api/sales')
def get_sales():
    page = request.args.get('page', 1, type=int)
    per_page = request.args.get('per_page', 20, type=int)
    q = request.args.get('q', '')
    payment = request.args.get('payment', '')

    # Cancelled sales stay in the database but leave the operational list.
    query = Sale.query.filter(Sale.cancelled_at.is_(None))
    if q:
        query = query.filter(Sale.id == int(q) if q.isdigit() else Sale.id == -1)
    if payment:
        query = query.filter_by(payment_method=payment)

    paginated = query.order_by(Sale.created_at.desc()).paginate(page=page, per_page=per_page, error_out=False)
    return jsonify({
        'sales': [s.to_dict() for s in paginated.items],
        'total': paginated.total,
        'pages': paginated.pages,
        'current_page': page,
    })

@sales_bp.route('/api/sales/<int:sid>')
def get_sale(sid):
    s = Sale.query.get_or_404(sid)
    data = s.to_dict()
    already_returned = {}
    for ret in s.returns:
        for r_item in ret.items:
            already_returned[r_item.product_id] = already_returned.get(r_item.product_id, 0) + r_item.quantity
    for item in data['items']:
        item['returned_quantity'] = already_returned.get(item['product_id'], 0)
    return jsonify(data)

@sales_bp.route('/api/sales/<int:sid>', methods=['DELETE'])
def delete_sale(sid):
    # DELETE guard: a sale is a financial record — never hard-delete it.
    # Cancellation is stamped in cancelled_at; stock restoration stays.
    s = Sale.query.get_or_404(sid)
    if s.cancelled_at is not None:
        return jsonify({'success': False,
                        'error': 'Sale is already cancelled'}), 400
    for item in s.items:
        item.product.stock += item.quantity
    s.cancelled_at = datetime.utcnow()
    db.session.commit()
    return jsonify({'success': True})

@sales_bp.route('/api/sales/<int:sale_id>/whatsapp', methods=['GET'])
def whatsapp_receipt(sale_id):
    from models import Sale, Customer
    sale = Sale.query.get_or_404(sale_id)
    customer = Customer.query.get(sale.customer_id) if sale.customer_id else None
    
    # Deployment hostname comes from the environment, never from code.
    # Fallback: the host this request arrived on (correct for local/dev).
    BASE_URL = os.environ.get('RECEIPT_BASE_URL', '').strip().rstrip('/') \
        or request.url_root.rstrip('/')

    lines = []
    lines.append(f"🧾 *Vignesh Growth Lab POS - Invoice #{sale.id:05d}*")
    lines.append(f"📅 {sale.created_at.strftime('%d-%m-%Y %I:%M %p')}")
    lines.append(f"💰 Total: ₹{sale.total:.2f} ({sale.payment_method.upper()})")
    lines.append(f"")
    lines.append(f"📄 View your tax invoice here:")
    lines.append(f"{BASE_URL}/receipt/{sale.id}")
    lines.append(f"")
    lines.append(f"Thank you for your business! 🙏")
    
    message = "\n".join(lines)
    phone = request.args.get('phone') or (customer.phone if customer else '')
    
    import urllib.parse
    if phone and phone != '0000000000':
        clean_phone = ''.join(filter(str.isdigit, phone))
        if len(clean_phone) == 10:
            clean_phone = '91' + clean_phone
        wa_url = f"https://wa.me/{clean_phone}?text={urllib.parse.quote(message)}"
    else:
        wa_url = f"https://wa.me/?text={urllib.parse.quote(message)}"
    
    return jsonify({'success': True, 'whatsapp_url': wa_url, 'message': message})


@sales_bp.route('/api/sales/<int:sale_id>/return', methods=['POST'])
def create_return(sale_id):
    sale = Sale.query.get_or_404(sale_id)
    data = request.get_json() or {}
    
    returned_items_req = data.get('items', [])
    payment_method = data.get('payment_method', 'cash')
    reason = data.get('reason', '')
    
    if not returned_items_req:
        return jsonify({'success': False, 'error': 'No items selected for return'}), 400
        
    # Calculate already returned quantities for this sale
    already_returned = {}
    for ret in sale.returns:
        for r_item in ret.items:
            already_returned[r_item.product_id] = already_returned.get(r_item.product_id, 0) + r_item.quantity
            
    # Map sale items for easy lookup
    sale_items_map = {item.product_id: item for item in sale.items}
    
    return_items_to_save = []
    total_refund = 0.0
    
    for req_item in returned_items_req:
        pid = int(req_item.get('product_id'))
        qty_to_return = int(req_item.get('quantity', 0))
        
        if qty_to_return <= 0:
            continue
            
        if pid not in sale_items_map:
            return jsonify({'success': False, 'error': f'Product {pid} was not part of this sale'}), 400
            
        sale_item = sale_items_map[pid]
        max_allowed = sale_item.quantity - already_returned.get(pid, 0)
        
        if qty_to_return > max_allowed:
            return jsonify({
                'success': False,
                'error': f'Cannot return {qty_to_return} of {sale_item.product.name}. Max returnable is {max_allowed}.'
            }), 400
            
        unit_price = sale_item.price
        subtotal = round(qty_to_return * unit_price, 2)
        total_refund += subtotal
        
        return_items_to_save.append({
            'product_id': pid,
            'quantity': qty_to_return,
            'price': unit_price,
            'subtotal': subtotal,
            'product': sale_item.product
        })
        
    if not return_items_to_save:
        return jsonify({'success': False, 'error': 'Invalid return quantities'}), 400
        
    # Create SaleReturn
    sale_return = SaleReturn(
        sale_id=sale.id,
        refund_amount=round(total_refund, 2),
        payment_method=payment_method,
        reason=reason,
        created_at=datetime.utcnow()
    )
    db.session.add(sale_return)
    db.session.flush() # get sale_return.id
    
    for item_data in return_items_to_save:
        ret_item = SaleReturnItem(
            sale_return_id=sale_return.id,
            product_id=item_data['product_id'],
            quantity=item_data['quantity'],
            price=item_data['price'],
            subtotal=item_data['subtotal']
        )
        db.session.add(ret_item)
        
        # Restore stock
        item_data['product'].stock += item_data['quantity']
        
    db.session.commit()
    
    return jsonify({
        'success': True,
        'message': 'Return processed successfully!',
        'refund_amount': round(total_refund, 2)
    })


@sales_bp.route('/receipt/<int:sale_id>')
def receipt_page(sale_id):
    from models import Sale
    sale = Sale.query.get_or_404(sale_id)
    return render_template('receipt.html', sale=sale)

import logging
from decimal import Decimal, ROUND_FLOOR, ROUND_HALF_UP

from flask import Blueprint, render_template, request, jsonify
from models import Product, Sale, SaleItem, Customer
from extensions import db
from datetime import datetime

pos_bp = Blueprint('pos', __name__)

logger = logging.getLogger(__name__)

TWO_PLACES = Decimal('0.01')

# [P0-3] Hard request limits. Values outside these are rejected with 400,
# never clamped.
MAX_QUANTITY_PER_LINE = 10000
MAX_LINES_PER_SALE = 200


def _parse_money(value):
    """Parse a JSON number into a 2-dp Decimal; None if it is not a plain number.

    bool is explicitly rejected because it is a subclass of int in Python and
    would otherwise silently pass as 0/1.
    """
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    try:
        return Decimal(str(value)).quantize(TWO_PLACES, rounding=ROUND_HALF_UP)
    except ArithmeticError:
        # NaN/Infinity survive json parsing but cannot be quantized.
        return None


def _apportion_discount(discount, line_gross):
    """[P0-4] Split an order-level discount across lines by gross value.

    Uses the largest-remainder method so the per-line parts sum EXACTLY to the
    discount — floor every share to the paisa, then hand the remaining paise
    to the lines with the largest fractional remainders (ties broken by line
    order for determinism). A naive per-line round() can lose or invent a
    paisa, which is exactly the reconciliation bug this fixes.
    """
    zero = Decimal('0.00')
    if discount == zero:
        return [zero] * len(line_gross)

    grand_total = sum(line_gross, zero)
    shares = []
    fractions = []
    floor_sum = zero
    for idx, gross in enumerate(line_gross):
        raw = discount * gross / grand_total
        floored = raw.quantize(TWO_PLACES, rounding=ROUND_FLOOR)
        shares.append(floored)
        floor_sum += floored
        fractions.append((raw - floored, idx))

    remaining_paise = int(((discount - floor_sum) / TWO_PLACES).to_integral_value())
    fractions.sort(key=lambda entry: (-entry[0], entry[1]))
    for _, idx in fractions[:remaining_paise]:
        shares[idx] += TWO_PLACES

    if sum(shares, zero) != discount:
        logger.error(
            'Discount apportionment failed: discount=%s shares=%s gross=%s',
            discount, shares, line_gross
        )
        raise ValueError('Discount apportionment does not sum to the discount')
    return shares


@pos_bp.route('/pos')
def pos():
    return render_template('pos.html', page='pos')


@pos_bp.route('/api/products/search')
def search_products():
    q = request.args.get('q', '').strip()
    category_id = request.args.get('category_id', type=int)
    query = Product.query.filter_by(active=True)
    if q:
        query = query.filter(
            (Product.name.ilike(f'%{q}%')) |
            (Product.sku.ilike(f'%{q}%')) |
            (Product.barcode.ilike(f'%{q}%')) |
            (Product.hsn_code.ilike(f'%{q}%'))
        )
    if category_id:
        query = query.filter_by(category_id=category_id)
    products = query.order_by(Product.name).limit(60).all()
    return jsonify([p.to_dict() for p in products])


@pos_bp.route('/api/checkout', methods=['POST'])
def checkout():
    data = request.get_json(silent=True)
    if not isinstance(data, dict):
        return jsonify({'error': 'Request body must be a JSON object'}), 400

    # ── [P0-3] Validate EVERYTHING before any mutation ──────────────────────
    items = data.get('items')
    if not items:
        return jsonify({'error': 'Cart is empty / கார்ட் காலியாக உள்ளது'}), 400
    if not isinstance(items, list):
        return jsonify({'error': 'items must be a list'}), 400
    if len(items) > MAX_LINES_PER_SALE:
        return jsonify({'error': f'Too many lines: maximum {MAX_LINES_PER_SALE} items per sale'}), 400

    for line_no, item in enumerate(items, start=1):
        if not isinstance(item, dict):
            return jsonify({'error': f'Line {line_no}: each item must be an object'}), 400
        product_id = item.get('product_id')
        if isinstance(product_id, bool) or not isinstance(product_id, int):
            return jsonify({'error': f'Line {line_no}: product_id must be an integer'}), 400
        qty = item.get('quantity')
        if isinstance(qty, bool) or not isinstance(qty, int):
            return jsonify({'error': f'Line {line_no}: quantity must be an integer'}), 400
        if qty <= 0:
            return jsonify({'error': f'Line {line_no}: quantity must be greater than 0'}), 400
        if qty > MAX_QUANTITY_PER_LINE:
            return jsonify({'error': f'Line {line_no}: quantity must not exceed {MAX_QUANTITY_PER_LINE}'}), 400

    amount_paid = _parse_money(data.get('amount_paid', 0))
    if amount_paid is None:
        return jsonify({'error': 'amount_paid must be a number'}), 400
    if amount_paid < Decimal('0.00'):
        return jsonify({'error': 'amount_paid must be greater than or equal to 0'}), 400

    discount = _parse_money(data.get('discount', 0))
    if discount is None:
        return jsonify({'error': 'discount must be a number'}), 400
    if discount < Decimal('0.00'):
        return jsonify({'error': 'discount must be greater than or equal to 0'}), 400

    customer_id = data.get('customer_id', 1)
    if customer_id is not None:
        if isinstance(customer_id, bool) or not isinstance(customer_id, int):
            return jsonify({'error': 'customer_id must be an integer'}), 400
        if Customer.query.get(customer_id) is None:
            return jsonify({'error': f'Customer {customer_id} does not exist'}), 400

    payment_method = data.get('payment_method', 'cash')
    notes = data.get('notes', '')

    # Load products, price lines, and check stock across ALL lines of the same
    # product so duplicated lines cannot oversell.
    sale_lines = []
    line_gross = []
    required_stock = {}
    for item in items:
        product = Product.query.get(item['product_id'])
        if not product:
            return jsonify({'error': 'Product not found'}), 404
        qty = item['quantity']
        required_stock[product.id] = required_stock.get(product.id, 0) + qty
        # Decimal from str(float) — never compute money in float. Columns are
        # still Float, so conversion back happens only at assignment below.
        gross = (Decimal(str(product.price)) * qty).quantize(TWO_PLACES, rounding=ROUND_HALF_UP)
        sale_lines.append((product, qty, gross))
        line_gross.append(gross)

    for item in items:
        product = next(p for p, _, _ in sale_lines if p.id == item['product_id'])
        if product.stock < required_stock[product.id]:
            return jsonify({'error': f'Stock insufficient: {product.name} — only {product.stock} left'}), 400

    grand_total = sum(line_gross, Decimal('0.00'))
    if discount > grand_total:
        return jsonify({'error': 'discount must not exceed the cart total'}), 400

    # ── [P0-4] Apportion discount, then tax the post-discount line value ────
    discount_shares = _apportion_discount(discount, line_gross)

    sub_total = Decimal('0.00')
    total_cgst = Decimal('0.00')
    total_sgst = Decimal('0.00')
    for (product, qty, gross), share in zip(sale_lines, discount_shares):
        net = gross - share
        rate = Decimal(str(product.gst_rate))
        taxable = (net / (Decimal('1') + rate / Decimal('100'))).quantize(
            TWO_PLACES, rounding=ROUND_HALF_UP)
        gst_amount = net - taxable
        cgst = (gst_amount / 2).quantize(TWO_PLACES, rounding=ROUND_HALF_UP)
        sgst = gst_amount - cgst
        sub_total += taxable
        total_cgst += cgst
        total_sgst += sgst

    total_after_discount = grand_total - discount

    # Invoice must reconcile before anything is written.
    reconciliation_gap = abs((sub_total + total_cgst + total_sgst) - total_after_discount)
    if reconciliation_gap > Decimal('0.01'):
        logger.error(
            'GST reconciliation failed: taxable=%s cgst=%s sgst=%s total=%s gap=%s items=%s discount=%s',
            sub_total, total_cgst, total_sgst, total_after_discount,
            reconciliation_gap, [(p.id, q) for p, q, _ in sale_lines], discount
        )
        raise ValueError('GST reconciliation failed: invoice does not add up')

    change = amount_paid - total_after_discount

    sale = Sale(
        customer_id=customer_id,
        subtotal=float(sub_total),
        total_cgst=float(total_cgst),
        total_sgst=float(total_sgst),
        total=float(total_after_discount),
        discount=float(discount),
        amount_paid=float(amount_paid),
        change_given=float(max(Decimal('0.00'), change)),
        payment_method=payment_method,
        notes=notes,
        created_at=datetime.utcnow()
    )
    db.session.add(sale)
    db.session.flush()

    for product, qty, gross in sale_lines:
        si = SaleItem(sale_id=sale.id, product_id=product.id, quantity=qty,
                      price=product.price, gst_rate=product.gst_rate,
                      subtotal=float(gross))
        db.session.add(si)
        product.stock -= qty

    # Loyalty points: 1 point per ₹10 spent
    if customer_id is not None:
        customer = Customer.query.get(customer_id)
        if customer:
            customer.points += int(total_after_discount // 10)

    db.session.commit()
    return jsonify({'success': True, 'sale': sale.to_dict(), 'receipt_id': sale.id})

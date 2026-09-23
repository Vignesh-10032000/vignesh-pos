from extensions import db
from datetime import datetime

class Category(db.Model):
    __tablename__ = 'categories'
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(100), nullable=False)
    icon = db.Column(db.String(10), default='📦')
    products = db.relationship('Product', backref='category', lazy=True)

    def to_dict(self):
        return {'id': self.id, 'name': self.name, 'icon': self.icon}


class Product(db.Model):
    __tablename__ = 'products'
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(200), nullable=False)
    sku = db.Column(db.String(50), unique=True, nullable=False)
    barcode = db.Column(db.String(50), unique=True)
    price = db.Column(db.Float, nullable=False)          # MRP incl. GST
    cost = db.Column(db.Float, default=0)
    stock = db.Column(db.Integer, default=0)
    low_stock_threshold = db.Column(db.Integer, default=10)
    category_id = db.Column(db.Integer, db.ForeignKey('categories.id'))
    gst_rate = db.Column(db.Float, default=0)            # 0, 5, 12, 18, 28
    hsn_code = db.Column(db.String(20), default='')      # HSN/SAC code
    is_veg = db.Column(db.Boolean, default=True)         # 🟢/🔴 veg/non-veg
    active = db.Column(db.Boolean, default=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    @property
    def price_excl_gst(self):
        return round(self.price / (1 + self.gst_rate / 100), 2)

    @property
    def gst_amount(self):
        return round(self.price - self.price_excl_gst, 2)

    @property
    def cgst(self):
        return round(self.gst_amount / 2, 2)

    @property
    def sgst(self):
        return round(self.gst_amount / 2, 2)

    def to_dict(self):
        return {
            'id': self.id,
            'name': self.name,
            'sku': self.sku,
            'barcode': self.barcode,
            'price': self.price,
            'cost': self.cost,
            'stock': self.stock,
            'low_stock_threshold': self.low_stock_threshold,
            'category_id': self.category_id,
            'category': self.category.name if self.category else '',
            'category_icon': self.category.icon if self.category else '📦',
            'gst_rate': self.gst_rate,
            'hsn_code': self.hsn_code,
            'is_veg': self.is_veg,
            'active': self.active,
        }


class Customer(db.Model):
    __tablename__ = 'customers'
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(200), nullable=False)
    email = db.Column(db.String(200))
    phone = db.Column(db.String(50))
    gstin = db.Column(db.String(20))                     # Customer GSTIN (B2B)
    points = db.Column(db.Integer, default=0)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    sales = db.relationship('Sale', backref='customer', lazy=True)

    def to_dict(self):
        return {
            'id': self.id,
            'name': self.name,
            'email': self.email,
            'phone': self.phone,
            'gstin': self.gstin or '',
            'points': self.points,
            'total_spent': sum(s.total for s in self.sales),
            'total_orders': len(self.sales),
        }


class Sale(db.Model):
    __tablename__ = 'sales'
    id = db.Column(db.Integer, primary_key=True)
    customer_id = db.Column(db.Integer, db.ForeignKey('customers.id'))
    subtotal = db.Column(db.Float, default=0)            # before GST
    total_cgst = db.Column(db.Float, default=0)
    total_sgst = db.Column(db.Float, default=0)
    total = db.Column(db.Float, default=0)               # incl. GST, after discount
    discount = db.Column(db.Float, default=0)
    amount_paid = db.Column(db.Float, default=0)
    change_given = db.Column(db.Float, default=0)
    payment_method = db.Column(db.String(50), default='cash')  # cash/card/upi
    notes = db.Column(db.Text)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    # Financial records are never hard-deleted; cancellation stamps this.
    # Existing databases: run scripts/add_cancelled_at_column.py once.
    cancelled_at = db.Column(db.DateTime, nullable=True)
    
    order_type = db.Column(db.String(20), default='COUNTER') # COUNTER / DINE_IN / TAKEAWAY
    table_id = db.Column(db.Integer, db.ForeignKey('dining_tables.id'), nullable=True)
    table = db.relationship('DiningTable', foreign_keys=[table_id])
    status = db.Column(db.String(20), default='COMPLETED')   # OPEN / BILLED / COMPLETED / CANCELLED
    opened_at = db.Column(db.DateTime, nullable=True)
    waiter_name = db.Column(db.String(100), nullable=True)

    items = db.relationship('SaleItem', backref='sale', lazy=True)

    def to_dict(self):
        return {
            'id': self.id,
            'customer': self.customer.name if self.customer else 'Walk-in',
            'customer_id': self.customer_id,
            'subtotal': self.subtotal,
            'total_cgst': self.total_cgst,
            'total_sgst': self.total_sgst,
            'total': self.total,
            'discount': self.discount,
            'amount_paid': self.amount_paid,
            'change_given': self.change_given,
            'payment_method': self.payment_method,
            'order_type': self.order_type,
            'table_id': self.table_id,
            'table_number': self.table.table_number if self.table else None,
            'status': self.status,
            'opened_at': self.opened_at.strftime('%d-%m-%Y %H:%M') if self.opened_at else None,
            'waiter_name': self.waiter_name,
            'items': [i.to_dict() for i in self.items],
            'item_count': sum(i.quantity for i in self.items),
            'created_at': self.created_at.strftime('%d-%m-%Y %H:%M'),
        }


class SaleItem(db.Model):
    __tablename__ = 'sale_items'
    id = db.Column(db.Integer, primary_key=True)
    sale_id = db.Column(db.Integer, db.ForeignKey('sales.id'), nullable=False)
    product_id = db.Column(db.Integer, db.ForeignKey('products.id'), nullable=False)
    quantity = db.Column(db.Integer, nullable=False)
    price = db.Column(db.Float, nullable=False)          # per unit incl. GST
    gst_rate = db.Column(db.Float, default=0)
    subtotal = db.Column(db.Float, nullable=False)       # qty × price incl. GST
    cooking_notes = db.Column(db.Text, nullable=True)
    product = db.relationship('Product')

    def to_dict(self):
        taxable = round(self.subtotal / (1 + self.gst_rate / 100), 2)
        gst_amt = round(self.subtotal - taxable, 2)
        return {
            'id': self.id,
            'product_id': self.product_id,
            'product_name': self.product.name if self.product else '',
            'hsn_code': self.product.hsn_code if self.product else '',
            'quantity': self.quantity,
            'price': self.price,
            'gst_rate': self.gst_rate,
            'taxable': taxable,
            'cgst': round(gst_amt / 2, 2),
            'sgst': round(gst_amt / 2, 2),
            'subtotal': self.subtotal,
            'cooking_notes': self.cooking_notes,
        }


class SaleReturn(db.Model):
    __tablename__ = 'sale_returns'
    id = db.Column(db.Integer, primary_key=True)
    sale_id = db.Column(db.Integer, db.ForeignKey('sales.id'), nullable=False)
    refund_amount = db.Column(db.Float, default=0)
    payment_method = db.Column(db.String(50), default='cash')
    reason = db.Column(db.Text)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    
    sale = db.relationship('Sale', backref=db.backref('returns', lazy=True))
    items = db.relationship('SaleReturnItem', backref='sale_return', lazy=True)

    def to_dict(self):
        return {
            'id': self.id,
            'sale_id': self.sale_id,
            'refund_amount': self.refund_amount,
            'payment_method': self.payment_method,
            'reason': self.reason,
            'created_at': self.created_at.strftime('%d-%m-%Y %H:%M'),
            'items': [i.to_dict() for i in self.items]
        }


class SaleReturnItem(db.Model):
    __tablename__ = 'sale_return_items'
    id = db.Column(db.Integer, primary_key=True)
    sale_return_id = db.Column(db.Integer, db.ForeignKey('sale_returns.id'), nullable=False)
    product_id = db.Column(db.Integer, db.ForeignKey('products.id'), nullable=False)
    quantity = db.Column(db.Integer, nullable=False)
    price = db.Column(db.Float, nullable=False)
    subtotal = db.Column(db.Float, nullable=False)

    product = db.relationship('Product')

    def to_dict(self):
        return {
            'id': self.id,
            'product_id': self.product_id,
            'product_name': self.product.name if self.product else '',
            'quantity': self.quantity,
            'price': self.price,
            'subtotal': self.subtotal
        }


class DiningTable(db.Model):
    __tablename__ = 'dining_tables'
    id = db.Column(db.Integer, primary_key=True)
    table_number = db.Column(db.String(10), unique=True, nullable=False)
    area = db.Column(db.String(50), default='Main Hall')
    capacity = db.Column(db.Integer, default=4)
    status = db.Column(db.String(20), default='AVAILABLE') # AVAILABLE / OCCUPIED / BILLED / RESERVED
    current_order_id = db.Column(db.Integer, db.ForeignKey('sales.id'), nullable=True)
    current_order = db.relationship('Sale', foreign_keys=[current_order_id])
    occupied_at = db.Column(db.DateTime, nullable=True)
    is_active = db.Column(db.Boolean, default=True)

    def to_dict(self):
        time_since = 0
        if self.occupied_at:
            delta = datetime.utcnow() - self.occupied_at
            time_since = int(delta.total_seconds() // 60)
            
        return {
            'id': self.id,
            'table_number': self.table_number,
            'area': self.area,
            'capacity': self.capacity,
            'status': self.status,
            'current_order_id': self.current_order_id,
            'occupied_at': self.occupied_at.isoformat() if self.occupied_at else None,
            'is_active': self.is_active,
            'time_since_occupied': time_since
        }


class KOT(db.Model):
    __tablename__ = 'kots'
    id = db.Column(db.Integer, primary_key=True)
    kot_number = db.Column(db.Integer, nullable=False)
    order_id = db.Column(db.Integer, db.ForeignKey('sales.id'), nullable=False)
    table_id = db.Column(db.Integer, db.ForeignKey('dining_tables.id'), nullable=True)
    table = db.relationship('DiningTable', foreign_keys=[table_id])
    waiter_name = db.Column(db.String(100), nullable=True)
    status = db.Column(db.String(20), default='SENT') # SENT / IN_PROGRESS / READY / SERVED
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    items = db.relationship('KOTItem', backref='kot', lazy=True)

    def to_dict(self):
        return {
            'id': self.id,
            'kot_number': self.kot_number,
            'order_id': self.order_id,
            'table_id': self.table_id,
            'waiter_name': self.waiter_name,
            'status': self.status,
            'created_at': self.created_at.isoformat() if self.created_at else None,
            'items': [i.to_dict() for i in self.items]
        }


class KOTItem(db.Model):
    __tablename__ = 'kot_items'
    id = db.Column(db.Integer, primary_key=True)
    kot_id = db.Column(db.Integer, db.ForeignKey('kots.id'), nullable=False)
    product_id = db.Column(db.Integer, db.ForeignKey('products.id'), nullable=False)
    quantity = db.Column(db.Integer, nullable=False)
    cooking_notes = db.Column(db.Text, nullable=True)

    product = db.relationship('Product')

    def to_dict(self):
        return {
            'id': self.id,
            'kot_id': self.kot_id,
            'product_id': self.product_id,
            'product_name': self.product.name if self.product else '',
            'quantity': self.quantity,
            'cooking_notes': self.cooking_notes,
        }

"""Restaurant POS single-location starter. Python 3.10+, FastAPI, SQLite.
All prices are integer cents/paisa. Payment methods are recorded only; no gateway.
"""
import base64
import hashlib
import hmac
import math
import os
import secrets
import sqlite3
import threading
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Literal

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

ROOT = Path(__file__).resolve().parent
DB_PATH = Path(os.environ.get('POS_DATABASE', str(ROOT / 'data' / 'restaurant.db'))).resolve()
DB_PATH.parent.mkdir(parents=True, exist_ok=True)
app = FastAPI(title='Service Restaurant POS', docs_url=None, redoc_url=None)
app.mount('/static', StaticFiles(directory=str(ROOT / 'static')), name='static')
SESSION_COOKIE = 'restaurant_pos_session'
SECURE_COOKIE = os.getenv('POS_SECURE_COOKIES', '0') == '1'
LOGIN_ATTEMPTS = {}
LOGIN_LOCK = threading.RLock()


@contextmanager
def database():
    db = sqlite3.connect(str(DB_PATH), timeout=15, isolation_level=None, check_same_thread=False)
    db.row_factory = sqlite3.Row
    db.execute('PRAGMA foreign_keys=ON')
    db.execute('PRAGMA busy_timeout=15000')
    try:
        yield db
    finally:
        db.close()


def init_db():
    with database() as db:
        db.executescript('''
        PRAGMA journal_mode=WAL;
        CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS users (
           id INTEGER PRIMARY KEY, name TEXT NOT NULL, username TEXT NOT NULL UNIQUE,
           password_hash TEXT NOT NULL, role TEXT NOT NULL CHECK(role IN ('admin','cashier','kitchen')),
           active INTEGER NOT NULL DEFAULT 1, created_at TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS sessions (
           token_hash TEXT PRIMARY KEY, user_id INTEGER NOT NULL REFERENCES users(id),
           csrf TEXT NOT NULL, expires_at TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS menu_items (
           id INTEGER PRIMARY KEY, name TEXT NOT NULL, category TEXT NOT NULL,
           price_cents INTEGER NOT NULL CHECK(price_cents>=0), sku TEXT NOT NULL DEFAULT '',
           stock INTEGER NOT NULL DEFAULT 0, track_stock INTEGER NOT NULL DEFAULT 0,
           active INTEGER NOT NULL DEFAULT 1, created_at TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS dining_tables (
           id INTEGER PRIMARY KEY, label TEXT NOT NULL UNIQUE,
           zone TEXT NOT NULL DEFAULT 'Main dining', seats INTEGER NOT NULL DEFAULT 4,
           active INTEGER NOT NULL DEFAULT 1);
        CREATE TABLE IF NOT EXISTS orders (
           id INTEGER PRIMARY KEY, order_number TEXT NOT NULL UNIQUE,
           service_type TEXT NOT NULL CHECK(service_type IN ('dine_in','takeaway','delivery')),
           table_id INTEGER REFERENCES dining_tables(id), customer_name TEXT NOT NULL DEFAULT '',
           note TEXT NOT NULL DEFAULT '', status TEXT NOT NULL DEFAULT 'new',
           kitchen_status TEXT NOT NULL DEFAULT 'new',
           subtotal_cents INTEGER NOT NULL, discount_cents INTEGER NOT NULL DEFAULT 0,
           tax_cents INTEGER NOT NULL DEFAULT 0, total_cents INTEGER NOT NULL,
           created_by INTEGER NOT NULL REFERENCES users(id), created_at TEXT NOT NULL,
           paid_at TEXT);
        CREATE TABLE IF NOT EXISTS order_items (
           id INTEGER PRIMARY KEY, order_id INTEGER NOT NULL REFERENCES orders(id),
           menu_item_id INTEGER REFERENCES menu_items(id), item_name TEXT NOT NULL,
           unit_price_cents INTEGER NOT NULL, quantity INTEGER NOT NULL CHECK(quantity>0),
           note TEXT NOT NULL DEFAULT '');
        CREATE TABLE IF NOT EXISTS payments (
           id INTEGER PRIMARY KEY, order_id INTEGER NOT NULL REFERENCES orders(id),
           method TEXT NOT NULL CHECK(method IN ('cash','card','other')),
           amount_cents INTEGER NOT NULL CHECK(amount_cents>0), created_at TEXT NOT NULL);
        CREATE INDEX IF NOT EXISTS ix_orders_date ON orders(created_at);
        CREATE INDEX IF NOT EXISTS ix_orders_table_status ON orders(table_id,status);
        CREATE INDEX IF NOT EXISTS ix_sessions_expiry ON sessions(expires_at);
        ''')
        if 'kitchen_status' not in [c['name'] for c in db.execute('PRAGMA table_info(orders)')]:
            db.execute("ALTER TABLE orders ADD COLUMN kitchen_status TEXT NOT NULL DEFAULT 'new'")
            db.execute("UPDATE orders SET kitchen_status=CASE WHEN status IN ('preparing','ready','served') THEN status ELSE 'new' END")

init_db()


def utcnow():
    return datetime.now(timezone.utc).isoformat(timespec='seconds')


def hash_password(password: str) -> str:
    salt = secrets.token_bytes(16)
    digest = hashlib.pbkdf2_hmac('sha256', password.encode(), salt, 310000)
    return f'pbkdf2_sha256$310000${salt.hex()}${digest.hex()}'


def verify_password(password: str, encoded: str) -> bool:
    try:
        alg, rounds, salt, digest = encoded.split('$')
        if alg != 'pbkdf2_sha256': return False
        raw = hashlib.pbkdf2_hmac('sha256', password.encode(), bytes.fromhex(salt), int(rounds))
        return hmac.compare_digest(raw, bytes.fromhex(digest))
    except (ValueError, TypeError):
        return False


def get_setting(db, key, default=''):
    found = db.execute('SELECT value FROM settings WHERE key=?', (key,)).fetchone()
    return found['value'] if found else default


def all_settings(db):
    return {r['key']:r['value'] for r in db.execute('SELECT * FROM settings')}


def put_setting(db, key, val):
    db.execute('INSERT INTO settings(key,value) VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value', (key,str(val)))


def is_configured(db):
    return db.execute('SELECT 1 FROM users LIMIT 1').fetchone() is not None


def auth(request: Request, db, roles=None, mutate=False):
    raw = request.cookies.get(SESSION_COOKIE, '')
    if not raw: raise HTTPException(401, 'Please sign in')
    digest = hashlib.sha256(raw.encode()).hexdigest()
    row = db.execute('''SELECT u.id,u.name,u.username,u.role,s.csrf,COALESCE(s.branch_id,u.branch_id,1) AS branch_id FROM sessions s
          JOIN users u ON u.id=s.user_id WHERE s.token_hash=? AND s.expires_at>? AND u.active=1''',
          (digest,utcnow())).fetchone()
    if not row: raise HTTPException(401, 'Session expired. Please sign in again')
    if roles and row['role'] not in roles: raise HTTPException(403, 'Not allowed for your role')
    if mutate and not hmac.compare_digest(request.headers.get('x-csrf-token',''), row['csrf']):
        raise HTTPException(403, 'Invalid security token')
    return dict(row)


def new_session(db, user_id, response: JSONResponse):
    token = secrets.token_urlsafe(36)
    csrf = secrets.token_urlsafe(32)
    db.execute('DELETE FROM sessions WHERE expires_at<?', (utcnow(),))
    expires = (datetime.now(timezone.utc) + timedelta(days=7)).isoformat(timespec='seconds')
    db.execute('INSERT INTO sessions(token_hash,user_id,csrf,expires_at,branch_id) VALUES (?,?,?,?,(SELECT branch_id FROM users WHERE id=?))',
               (hashlib.sha256(token.encode()).hexdigest(),user_id,csrf,expires,user_id))
    response.set_cookie(SESSION_COOKIE,token,max_age=604800,httponly=True,secure=SECURE_COOKIE,samesite='strict',path='/')
    return csrf


def require_text(value: str, label: str, length=120):
    value = value.strip()
    if not value or len(value)>length: raise HTTPException(422, f'{label} is required (up to {length} characters)')
    return value


def money_cents(value: Any, field='Amount', minimum=0, maximum=100000000):
    try:
        # Receive integer cents throughout API, reject booleans/float/fractional values.
        if type(value) is not int or value < minimum or value > maximum: raise ValueError
        return value
    except (ValueError,TypeError):
        raise HTTPException(422, f'{field} is invalid')


@app.get('/')
def homepage(): return FileResponse(ROOT/'static'/'index.html')


@app.get('/api/session')
def session(request: Request):
    with database() as db:
        if not is_configured(db): return {'configured':False}
        try: user = auth(request,db)
        except HTTPException:
            from branding import public_branding
            return {'configured':True,'authenticated':False,'restaurant':get_setting(db,'restaurant','Restaurant'),
                    'branding':public_branding(db,1)}
        return {'configured':True,'authenticated':True,'user':{k:user[k] for k in ('id','name','username','role','branch_id')},
                'csrf':user['csrf'],'settings':all_settings(db)}


class Setup(BaseModel):
    restaurant: str
    name: str
    username: str
    password: str
    currency: str = 'PKR'


@app.post('/api/setup')
def setup(payload:Setup):
    with database() as db:
        db.execute('BEGIN IMMEDIATE')
        if is_configured(db): raise HTTPException(409,'System is already configured')
        restaurant=require_text(payload.restaurant,'Restaurant name')
        name=require_text(payload.name,'Your name')
        username=require_text(payload.username,'Username',48).lower()
        if len(payload.password)<8: raise HTTPException(422,'Password needs at least 8 characters')
        if len(payload.currency.strip()) != 3 or not payload.currency.strip().isalpha(): raise HTTPException(422,'Invalid currency code')
        cursor=db.execute('INSERT INTO users(name,username,password_hash,role,created_at) VALUES (?,?,?,?,?)',
                   (name,username,hash_password(payload.password),'admin',utcnow()))
        for k,v in {'restaurant':restaurant,'currency':payload.currency.upper(),'tax_percent':'0','receipt_footer':'Thank you for dining with us!','address':''}.items(): put_setting(db,k,v)
        demo_menu=[('Classic Burger','Mains',129900,'BURG-01'),('Margherita Pizza','Mains',189900,'PIZZ-01'),
                   ('Grilled Chicken','Mains',159900,'CHIK-01'),('Crispy Fries','Sides',49900,'SIDE-01'),
                   ('Garden Salad','Sides',64900,'SIDE-02'),('Chocolate Brownie','Desserts',69900,'DESS-01'),
                   ('Fresh Lemonade','Drinks',39900,'DRIN-01'),('Iced Coffee','Drinks',54900,'DRIN-02')]
        for title,cat,price,sku in demo_menu:
            db.execute('INSERT INTO menu_items(name,category,price_cents,sku,created_at) VALUES (?,?,?,?,?)',(title,cat,price,sku,utcnow()))
        for i in range(1,13):
            db.execute('INSERT INTO dining_tables(label,zone,seats,qr_token) VALUES (?,?,?,?)',(f'T{i:02}', 'Main dining' if i<=8 else 'Terrace', 4 if i<=8 else 2,secrets.token_urlsafe(24)))
        response=JSONResponse({'ok':True})
        new_session(db,cursor.lastrowid,response)
        db.commit()
        return response


class Login(BaseModel):
    username:str
    password:str


@app.post('/api/login')
def login(payload:Login, request:Request):
    import time
    ip=request.client.host if request.client else 'unknown'
    key=(ip,payload.username.strip().lower())
    now=time.monotonic()
    with LOGIN_LOCK:
        hits=[t for t in LOGIN_ATTEMPTS.get(key,[]) if now-t<300]
        if len(hits)>=6:raise HTTPException(429,'Too many sign-in attempts. Try again later')
        LOGIN_ATTEMPTS[key]=hits
    with database() as db:
        user=db.execute('SELECT * FROM users WHERE username=? AND active=1',(payload.username.strip().lower(),)).fetchone()
        if not user or not verify_password(payload.password,user['password_hash']):
            with LOGIN_LOCK:
                LOGIN_ATTEMPTS.setdefault(key,[]).append(now)
                if len(LOGIN_ATTEMPTS)>5000:LOGIN_ATTEMPTS.clear()
            raise HTTPException(401,'Incorrect username or password')
        with LOGIN_LOCK:LOGIN_ATTEMPTS.pop(key,None)
        response=JSONResponse({'ok':True})
        new_session(db,user['id'],response)
        return response


@app.post('/api/logout')
def logout(request:Request):
    with database() as db:
        user=auth(request,db,mutate=True)
        token=request.cookies.get(SESSION_COOKIE,'')
        db.execute('DELETE FROM sessions WHERE token_hash=?',(hashlib.sha256(token.encode()).hexdigest(),))
    response=JSONResponse({'ok':True})
    response.delete_cookie(SESSION_COOKIE,path='/')
    return response


def order_json(db,row):
    o=dict(row)
    o['items']=[dict(x) for x in db.execute('SELECT * FROM order_items WHERE order_id=? ORDER BY id',(o['id'],))]
    o['payments']=[dict(x) for x in db.execute('SELECT * FROM payments WHERE order_id=? ORDER BY id',(o['id'],))]
    o['refunds']=[dict(x) for x in db.execute('SELECT * FROM refunds WHERE order_id=? ORDER BY id',(o['id'],))]
    return o


def get_order(db,oid,branch_id=None):
    row=db.execute('''SELECT o.*,t.label AS table_label,u.name AS creator_name FROM orders o
        LEFT JOIN dining_tables t ON t.id=o.table_id
        LEFT JOIN users u ON u.id=o.created_by WHERE o.id=? AND (? IS NULL OR o.branch_id=?)''',(oid,branch_id,branch_id)).fetchone()
    if not row: raise HTTPException(404,'Order not found')
    return order_json(db,row)


@app.get('/api/bootstrap')
def bootstrap(request:Request):
    with database() as db:
        user=auth(request,db)
        return {'settings':all_settings(db),
          'menu':[dict(x) for x in db.execute('SELECT * FROM menu_items WHERE branch_id=? ORDER BY category,name',(user['branch_id'],))],
          'tables':[dict(x) for x in db.execute('SELECT * FROM dining_tables WHERE branch_id=? ORDER BY label',(user['branch_id'],))],
          'orders':[order_json(db,r) for r in db.execute('''SELECT o.*, t.label AS table_label,u.name AS creator_name FROM orders o
                  LEFT JOIN dining_tables t ON t.id=o.table_id LEFT JOIN users u ON u.id=o.created_by
                  WHERE o.branch_id=? ORDER BY o.id DESC LIMIT 250''',(user['branch_id'],))],
          'staff':[dict(x) for x in db.execute('SELECT id,name,username,role,active,created_at,branch_id FROM users WHERE branch_id=? OR ?=1 ORDER BY id',(user['branch_id'], int(user['role']=='admin')))]}


class MenuItem(BaseModel):
    id:int|None = None
    name:str
    category:str
    price_cents:int
    sku:str = ''
    stock:int = 0
    track_stock:bool = False
    active:bool = True


@app.post('/api/menu')
def save_menu(payload:MenuItem,request:Request):
    with database() as db:
        user=auth(request,db,['admin'],True)
        name=require_text(payload.name,'Item name')
        category=require_text(payload.category,'Category',60)
        price=money_cents(payload.price_cents,'Price')
        if payload.stock<0 or payload.stock>1000000: raise HTTPException(422,'Stock must be between 0 and 1,000,000')
        if payload.id:
            if not db.execute('SELECT 1 FROM menu_items WHERE id=? AND branch_id=?',(payload.id,user['branch_id'])).fetchone(): raise HTTPException(404,'Item not found')
            db.execute('''UPDATE menu_items SET name=?,category=?,price_cents=?,sku=?,stock=?,track_stock=?,active=? WHERE id=? AND branch_id=?''',
                       (name,category,price,payload.sku.strip()[:60],payload.stock,int(payload.track_stock),int(payload.active),payload.id,user['branch_id']))
        else:
            db.execute('''INSERT INTO menu_items(name,category,price_cents,sku,stock,track_stock,active,created_at,branch_id)
                    VALUES(?,?,?,?,?,?,?,?,?)''',(name,category,price,payload.sku.strip()[:60],payload.stock,int(payload.track_stock),int(payload.active),utcnow(),user['branch_id']))
        from enhancements import log
        log(db,user,'menu_save','menu',payload.id or 'new',name)
        return {'ok':True}


class DiningTable(BaseModel):
    id:int|None=None
    label:str
    zone:str='Main dining'
    seats:int=4
    active:bool=True


@app.post('/api/tables')
def save_table(payload:DiningTable,request:Request):
    with database() as db:
        user=auth(request,db,['admin'],True)
        name=require_text(payload.label,'Table label',20)
        zone=require_text(payload.zone,'Area',50)
        if not 1<=payload.seats<=30: raise HTTPException(422,'Invalid seating capacity')
        try:
            if payload.id:
                db.execute('UPDATE dining_tables SET label=?,zone=?,seats=?,active=? WHERE id=? AND branch_id=?',
                           (name,zone,payload.seats,int(payload.active),payload.id,user['branch_id']))
            else:
                db.execute('INSERT INTO dining_tables(label,zone,seats,active,branch_id,qr_token) VALUES(?,?,?,?,?,?)',
                           (name,zone,payload.seats,int(payload.active),user['branch_id'],secrets.token_urlsafe(24)))
        except sqlite3.IntegrityError: raise HTTPException(409,'That table label already exists')
        from enhancements import log
        log(db,user,'table_save','table',payload.id or 'new',name)
        return {'ok':True}


class OrderLine(BaseModel):
    item_id:int
    quantity:int=Field(ge=1,le=99)
    note:str=''


class OrderCreate(BaseModel):
    service_type:Literal['dine_in','takeaway','delivery']
    table_id:int|None=None
    customer_name:str=''
    note:str=''
    delivery_address:str=''
    delivery_phone:str=''
    discount_cents:int=0
    items:list[OrderLine]=Field(min_length=1,max_length=99)
    client_key:str|None=None
    customer_id:int|None=None


def calc_order(db,items,discount,branch_id=1):
    if len(items)>99: raise HTTPException(422,'Too many order items')
    subtotal=0
    lines=[]
    for item in items:
        product=db.execute('SELECT * FROM menu_items WHERE id=? AND active=1 AND branch_id=?',(item.item_id,branch_id)).fetchone()
        if not product: raise HTTPException(422,f'Menu item {item.item_id} is unavailable')
        if product['track_stock'] and item.quantity>product['stock']:
            raise HTTPException(422,f'Insufficient stock for {product["name"]}')
        subtotal+=product['price_cents']*item.quantity
        lines.append((product['id'],product['name'],product['price_cents'],item.quantity,item.note.strip()[:300]))
    discount=money_cents(discount,'Discount')
    if discount>subtotal: raise HTTPException(422,'Discount exceeds subtotal')
    try: tax_rate=float(get_setting(db,'tax_percent','0'))
    except ValueError: tax_rate=0
    tax=round((subtotal-discount)*tax_rate/100)
    return lines,subtotal,discount,tax,subtotal-discount+tax


@app.post('/api/orders')
def create_order(payload:OrderCreate,request:Request):
    with database() as db:
        user=auth(request,db,['admin','cashier'],True)
        db.execute('BEGIN IMMEDIATE')
        if payload.client_key:
            if len(payload.client_key)>80: raise HTTPException(422,'Client key too long')
            existing=db.execute('SELECT id FROM orders WHERE client_key=? AND branch_id=?',(payload.client_key,user['branch_id'])).fetchone()
            if existing: return {'ok':True,'order':get_order(db,existing['id'],user['branch_id']),'deduplicated':True}
        if payload.customer_id and not db.execute('SELECT id FROM customers WHERE id=? AND branch_id=?',(payload.customer_id,user['branch_id'])).fetchone(): raise HTTPException(422,'Customer not found')
        if payload.service_type=='dine_in':
            if not payload.table_id: raise HTTPException(422,'Choose a table for dine-in orders')
            table=db.execute('SELECT * FROM dining_tables WHERE id=? AND active=1 AND branch_id=?',(payload.table_id,user['branch_id'])).fetchone()
            if not table: raise HTTPException(422,'Invalid dining table')
            in_use=db.execute("SELECT id FROM orders WHERE table_id=? AND branch_id=? AND status NOT IN ('paid','void') LIMIT 1",(payload.table_id,user['branch_id'])).fetchone()
            if in_use: raise HTTPException(409,'Table is currently occupied')
        else:
            payload.table_id=None
        lines,subtotal,discount,tax,total=calc_order(db,payload.items,payload.discount_cents,user['branch_id'])
        if total<=0: raise HTTPException(422,'The order total must be greater than zero')
        created=utcnow()
        # Temporary unique number used until autoincrement ID is known.
        temp='TMP-'+secrets.token_hex(12)
        cur=db.execute('''INSERT INTO orders(order_number,service_type,table_id,customer_name,note,status,
                subtotal_cents,discount_cents,tax_cents,total_cents,created_by,created_at,branch_id,client_key,customer_id)
                VALUES(?,?,?,?,?,'new',?,?,?,?,?,?,?,?,?)''',
                (temp,payload.service_type,payload.table_id,payload.customer_name.strip()[:100],payload.note.strip()[:1000],
                 subtotal,discount,tax,total,user['id'],created,user['branch_id'],payload.client_key,payload.customer_id))
        oid=cur.lastrowid
        order_no=f'POS-{datetime.now(timezone.utc):%Y%m%d}-{oid:05d}'
        db.execute('UPDATE orders SET order_number=? WHERE id=?',(order_no,oid))
        for line in lines:
            db.execute('INSERT INTO order_items(order_id,menu_item_id,item_name,unit_price_cents,quantity,note) VALUES(?,?,?,?,?,?)',(oid,*line))
        if payload.service_type=='delivery':
            if not payload.delivery_phone.strip() or not payload.delivery_address.strip():
                raise HTTPException(422,'Delivery phone and address are required')
            db.execute('INSERT INTO deliveries(branch_id,order_id,address,phone,status,updated_at) VALUES(?,?,?,?,?,?)',
                       (user['branch_id'],oid,require_text(payload.delivery_address,'Delivery address',300),
                        require_text(payload.delivery_phone,'Delivery phone',40),'pending',utcnow()))
        from enhancements import log
        log(db,user,'order_created','order',oid,order_no)
        db.commit()
        return {'ok':True,'order':get_order(db,oid,user['branch_id'])}


class OrderAppend(BaseModel):
    items:list[OrderLine]=Field(min_length=1,max_length=99)
    note:str=''


@app.post('/api/orders/{oid}/items')
def append_order_items(oid:int,payload:OrderAppend,request:Request):
    with database() as db:
        user=auth(request,db,['admin','cashier'],True)
        db.execute('BEGIN IMMEDIATE')
        order=db.execute('SELECT * FROM orders WHERE id=? AND branch_id=?',(oid,user['branch_id'])).fetchone()
        if not order: raise HTTPException(404,'Order not found')
        if order['status'] in ('paid','void'): raise HTTPException(409,'Cannot edit a paid or void order')
        lines,added_subtotal,_,_,_=calc_order(db,payload.items,0,user['branch_id'])
        for line in lines:
            db.execute('INSERT INTO order_items(order_id,menu_item_id,item_name,unit_price_cents,quantity,note) VALUES(?,?,?,?,?,?)',(oid,*line))
        subtotal=order['subtotal_cents']+added_subtotal
        discount=order['discount_cents']
        try: tax_rate=float(get_setting(db,'tax_percent','0'))
        except ValueError: tax_rate=0
        tax=round((subtotal-discount)*tax_rate/100)
        newnote=order['note']
        if payload.note.strip():newnote=(newnote+' / '+payload.note.strip()).strip(' /')[:1000]
        db.execute("UPDATE orders SET subtotal_cents=?,tax_cents=?,total_cents=?,note=?,kitchen_status='new',status='new' WHERE id=?",
                   (subtotal,tax,subtotal-discount+tax,newnote,oid))
        from enhancements import log
        log(db,user,'order_items_added','order',oid,str(len(lines)))
        db.commit()
        return {'ok':True,'order':get_order(db,oid,user['branch_id'])}


class StatusChange(BaseModel):
    status:Literal['new','preparing','ready','served']


@app.post('/api/orders/{oid}/status')
def change_status(oid:int,payload:StatusChange,request:Request):
    with database() as db:
        user=auth(request,db,['admin','cashier','kitchen'],True)
        row=db.execute('SELECT status FROM orders WHERE id=? AND branch_id=?',(oid,user['branch_id'])).fetchone()
        if not row: raise HTTPException(404,'Order not found')
        if row['status']=='void': raise HTTPException(409,'This order is void')
        # Paid orders stay in the kitchen queue until prepared and served.
        db.execute("UPDATE orders SET kitchen_status=?,status=CASE WHEN status='paid' THEN 'paid' ELSE ? END WHERE id=?",
                   (payload.status,payload.status,oid))
        from enhancements import log
        log(db,user,'kitchen_status','order',oid,payload.status)
        return {'ok':True}


class Checkout(BaseModel):
    payments:list[dict]=Field(min_length=1,max_length=6)


@app.post('/api/orders/{oid}/checkout')
def checkout(oid:int,payload:Checkout,request:Request):
    with database() as db:
        user=auth(request,db,['admin','cashier'],True)
        db.execute('BEGIN IMMEDIATE')
        o=db.execute('SELECT * FROM orders WHERE id=? AND branch_id=?',(oid,user['branch_id'])).fetchone()
        if not o: raise HTTPException(404,'Order not found')
        if o['status'] in ('paid','void'): raise HTTPException(409,'Order is already closed')
        if user['role']=='cashier' and not db.execute('SELECT 1 FROM shifts WHERE user_id=? AND branch_id=? AND closed_at IS NULL',(user['id'],user['branch_id'])).fetchone():
            raise HTTPException(409,'Open a cashier shift before taking payments')
        total=0
        parsed=[]
        for entry in payload.payments:
            method=entry.get('method')
            if method not in ('cash','card','other'): raise HTTPException(422,'Invalid payment method')
            amount=money_cents(entry.get('amount_cents'),'Payment',1)
            total+=amount
            parsed.append((method,amount))
        if total!=o['total_cents']:
            raise HTTPException(422,'Payments must equal the exact total. Cash change is calculated in the browser')
        # Aggregate quantities before stock validation, under a single write lock.
        for line in db.execute('''SELECT i.menu_item_id,SUM(i.quantity) quantity,m.name,m.track_stock,m.stock
              FROM order_items i JOIN menu_items m ON m.id=i.menu_item_id
              WHERE i.order_id=? GROUP BY i.menu_item_id''',(oid,)):
            if line['track_stock'] and line['stock']<line['quantity']:
                raise HTTPException(409,f'Insufficient stock for {line["name"]}')
            if line['track_stock']:
                db.execute('UPDATE menu_items SET stock=stock-? WHERE id=?',(line['quantity'],line['menu_item_id']))
        # Ingredient recipe consumption and ingredient cost snapshot are atomic with payment.
        ingredients=list(db.execute('''SELECT r.ingredient_id,SUM(r.quantity_milli*i.quantity) qty,
                   MAX(g.name) name,MAX(g.cost_cents) unit_cost
                   FROM order_items i JOIN recipes r ON r.menu_item_id=i.menu_item_id
                   JOIN ingredients g ON g.id=r.ingredient_id
                   WHERE i.order_id=? AND g.branch_id=? GROUP BY r.ingredient_id''',(oid,user['branch_id'])))
        cogs=0
        for ing in ingredients:
            n=db.execute('UPDATE ingredients SET stock_milli=stock_milli-? WHERE id=? AND stock_milli>=?',
                         (ing['qty'],ing['ingredient_id'],ing['qty']))
            if n.rowcount!=1:raise HTTPException(409,f'Insufficient ingredient stock: {ing["name"]}')
            cogs+=round(ing['qty']*ing['unit_cost']/1000)
            db.execute('INSERT INTO stock_movements(branch_id,ingredient_id,quantity_milli,reason,reference,created_at) VALUES(?,?,?,?,?,?)',
                       (user['branch_id'],ing['ingredient_id'],-ing['qty'],'order_paid',str(oid),utcnow()))
            db.execute('INSERT INTO order_stock_usage(order_id,ingredient_id,quantity_milli) VALUES(?,?,?)',
                       (oid,ing['ingredient_id'],ing['qty']))
        for method,amount in parsed:
            db.execute('INSERT INTO payments(order_id,method,amount_cents,created_at,recorded_by) VALUES(?,?,?,?,?)',
                       (oid,method,amount,utcnow(),user['id']))
        db.execute("UPDATE orders SET status='paid',paid_at=?,cogs_cents=? WHERE id=?",(utcnow(),cogs,oid))
        if o['customer_id']:
            # 1 reward point per 100 full currency units in eligible net ticket value.
            points=o['total_cents']//10000
            if points:
                db.execute('INSERT INTO loyalty_ledger(branch_id,customer_id,order_id,points,reason,created_at) VALUES(?,?,?,?,?,?)',
                           (user['branch_id'],o['customer_id'],oid,points,'earn',utcnow()))
        from enhancements import log
        log(db,user,'checkout','order',oid,f'Paid {total} / ingredient cost {cogs}')
        db.commit()
        return {'ok':True,'order':get_order(db,oid,user['branch_id'])}


@app.post('/api/orders/{oid}/void')
def void_order(oid:int,request:Request):
    with database() as db:
        user=auth(request,db,['admin'],True)
        o=db.execute('SELECT status FROM orders WHERE id=? AND branch_id=?',(oid,user['branch_id'])).fetchone()
        if not o: raise HTTPException(404,'Order not found')
        if o['status']=='paid':raise HTTPException(409,'Paid orders cannot be voided. Handle refunds through your payment provider')
        db.execute("UPDATE orders SET status='void' WHERE id=?",(oid,))
        from enhancements import log
        log(db,user,'order_void','order',oid)
        return {'ok':True}


@app.get('/api/orders/{oid}/receipt')
def receipt(oid:int,request:Request):
    with database() as db:
        user=auth(request,db)
        return {'order':get_order(db,oid,user['branch_id']),'settings':all_settings(db)}


@app.get('/api/reports')
def reports(request:Request,date:str=''):
    with database() as db:
        user=auth(request,db,['admin','cashier'])
        if not date:date=datetime.now().date().isoformat()
        try: datetime.strptime(date,'%Y-%m-%d')
        except ValueError:raise HTTPException(422,'Invalid date')
        orders=[dict(o) for o in db.execute("SELECT * FROM orders WHERE substr(paid_at,1,10)=? AND status='paid' AND branch_id=?",(date,user['branch_id']))]
        sales=sum(x['total_cents'] for x in orders)
        discounts=sum(x['discount_cents'] for x in orders)
        taxes=sum(x['tax_cents'] for x in orders)
        payments=[dict(x) for x in db.execute('''SELECT p.method,SUM(p.amount_cents) amount_cents FROM payments p
               JOIN orders o ON o.id=p.order_id WHERE substr(o.paid_at,1,10)=? AND o.status='paid' AND o.branch_id=? GROUP BY p.method''',(date,user['branch_id']))]
        top=[dict(x) for x in db.execute('''SELECT i.item_name,SUM(i.quantity) quantity,SUM(i.unit_price_cents*i.quantity) gross_cents
             FROM order_items i JOIN orders o ON o.id=i.order_id WHERE substr(o.paid_at,1,10)=?
             AND o.status='paid' AND o.branch_id=? GROUP BY i.item_name ORDER BY quantity DESC LIMIT 8''',(date,user['branch_id']))]
        hourly=[dict(x) for x in db.execute('''SELECT substr(paid_at,12,2) hour, SUM(total_cents) sales_cents
                  FROM orders WHERE substr(paid_at,1,10)=? AND status='paid' AND branch_id=? GROUP BY hour ORDER BY hour''',(date,user['branch_id']))]
        return {'date':date,'sales_cents':sales,'orders':len(orders),'average_cents':round(sales/len(orders)) if orders else 0,
                'discounts_cents':discounts,'taxes_cents':taxes,'payments':payments,'top_items':top,'hourly':hourly}


class Settings(BaseModel):
    restaurant:str
    currency:str='PKR'
    tax_percent:float=0
    address:str=''
    receipt_footer:str='Thank you for dining with us!'


@app.post('/api/settings')
def settings(payload:Settings,request:Request):
    with database() as db:
        user=auth(request,db,['admin'],True)
        if not 0<=payload.tax_percent<=50 or not math.isfinite(payload.tax_percent):raise HTTPException(422,'Tax rate must be 0–50%')
        if len(payload.currency.strip())!=3 or not payload.currency.strip().isalpha():raise HTTPException(422,'Invalid currency')
        vals={'restaurant':require_text(payload.restaurant,'Restaurant name'),'currency':payload.currency.upper(),
              'tax_percent':str(payload.tax_percent),'address':payload.address.strip()[:300],
              'receipt_footer':payload.receipt_footer.strip()[:300]}
        for k,v in vals.items(): put_setting(db,k,v)
        from enhancements import log
        log(db,user,'settings_update','settings',user['branch_id'])
        return {'ok':True}


class Staff(BaseModel):
    name:str
    username:str
    password:str
    role:Literal['admin','cashier','kitchen']='cashier'


@app.post('/api/staff')
def create_staff(payload:Staff,request:Request):
    with database() as db:
        user=auth(request,db,['admin'],True)
        if len(payload.password)<8:raise HTTPException(422,'Password must have at least 8 characters')
        try:
            cur=db.execute('INSERT INTO users(name,username,password_hash,role,created_at,branch_id) VALUES(?,?,?,?,?,?)',
                       (require_text(payload.name,'Name'),require_text(payload.username,'Username',48).lower(),
                        hash_password(payload.password),payload.role,utcnow(),user['branch_id']))
            from enhancements import log
            log(db,user,'staff_create','user',cur.lastrowid,payload.role)
        except sqlite3.IntegrityError: raise HTTPException(409,'Username already exists')
        return {'ok':True}


@app.get('/api/health')
def health(): return {'status':'ok'}


@app.exception_handler(HTTPException)
def http_error(request:Request,exc:HTTPException):
    return JSONResponse({'error':exc.detail},status_code=exc.status_code)


@app.exception_handler(sqlite3.DatabaseError)
def database_error(request:Request,exc:sqlite3.DatabaseError):
    # Do not disclose SQL or paths to users.
    return JSONResponse({'error':'Database error. Please contact the administrator'},status_code=500)


# Optional advanced features and safe schema migrations.
from enhancements import install as _install_enhancements
_install_enhancements(app)

# Branch-specific visual themes and restaurant logo uploads.
from branding import install as _install_branding
_install_branding(app)

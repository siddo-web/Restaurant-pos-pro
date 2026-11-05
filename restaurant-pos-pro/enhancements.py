"""Advanced Restaurant POS extensions: migrations, shifts, kitchen stream, audit,
recipe stock, customer loyalty, QR/web ordering, delivery and branch-scoped reports.
Amounts are integer *minor currency units*; ingredient quantity is milli-units.
This is an installable reference implementation, not a certified fiscal/payment system.
"""
import asyncio
import csv
import io
import json
import os
import re
import secrets
import sqlite3
import threading
import time
from datetime import datetime, timezone
from typing import Literal
from fastapi import HTTPException, Request
from fastapi.responses import StreamingResponse, Response, FileResponse
from pydantic import BaseModel, Field

# Imported after app.py defines the shared helpers and all original routes.
from app import database, auth, utcnow, money_cents, require_text, get_setting, get_order, calc_order, OrderLine, ROOT

SCHEMA = """
CREATE TABLE IF NOT EXISTS branches (
  id INTEGER PRIMARY KEY, name TEXT NOT NULL, code TEXT NOT NULL UNIQUE,
  public_token TEXT NOT NULL UNIQUE, active INTEGER NOT NULL DEFAULT 1, created_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS audits (
  id INTEGER PRIMARY KEY, branch_id INTEGER NOT NULL, user_id INTEGER, actor TEXT NOT NULL,
  action TEXT NOT NULL, entity TEXT NOT NULL, entity_id TEXT NOT NULL,
  detail TEXT NOT NULL DEFAULT '', created_at TEXT NOT NULL);
CREATE INDEX IF NOT EXISTS ix_audit_branch_created ON audits(branch_id,id DESC);
CREATE TABLE IF NOT EXISTS shifts (
  id INTEGER PRIMARY KEY, branch_id INTEGER NOT NULL, user_id INTEGER NOT NULL,
  opening_cash_cents INTEGER NOT NULL, closing_cash_cents INTEGER,
  expected_cash_cents INTEGER, variance_cents INTEGER, opened_at TEXT NOT NULL,
  closed_at TEXT, note TEXT NOT NULL DEFAULT '');
CREATE UNIQUE INDEX IF NOT EXISTS ux_open_shift_per_user ON shifts(user_id) WHERE closed_at IS NULL;
CREATE TABLE IF NOT EXISTS refunds (
  id INTEGER PRIMARY KEY, order_id INTEGER NOT NULL REFERENCES orders(id),
  branch_id INTEGER NOT NULL, user_id INTEGER NOT NULL, method TEXT NOT NULL,
  amount_cents INTEGER NOT NULL CHECK(amount_cents>0), reason TEXT NOT NULL,
  restock INTEGER NOT NULL DEFAULT 0, cogs_reversal_cents INTEGER NOT NULL DEFAULT 0, created_at TEXT NOT NULL);
CREATE INDEX IF NOT EXISTS ix_refunds_date ON refunds(branch_id,created_at);
CREATE TABLE IF NOT EXISTS ingredients (
  id INTEGER PRIMARY KEY, branch_id INTEGER NOT NULL, name TEXT NOT NULL,
  unit TEXT NOT NULL CHECK(unit IN ('g','ml','pcs')),
  stock_milli INTEGER NOT NULL DEFAULT 0 CHECK(stock_milli>=0),
  low_stock_milli INTEGER NOT NULL DEFAULT 0 CHECK(low_stock_milli>=0),
  cost_cents INTEGER NOT NULL DEFAULT 0 CHECK(cost_cents>=0),
  active INTEGER NOT NULL DEFAULT 1);
CREATE INDEX IF NOT EXISTS ix_ingredients_branch ON ingredients(branch_id);
CREATE TABLE IF NOT EXISTS recipes (
  menu_item_id INTEGER NOT NULL REFERENCES menu_items(id),
  ingredient_id INTEGER NOT NULL REFERENCES ingredients(id),
  quantity_milli INTEGER NOT NULL CHECK(quantity_milli>0),
  PRIMARY KEY(menu_item_id,ingredient_id));
CREATE TABLE IF NOT EXISTS order_stock_usage (
  order_id INTEGER NOT NULL REFERENCES orders(id),
  ingredient_id INTEGER NOT NULL REFERENCES ingredients(id),
  quantity_milli INTEGER NOT NULL CHECK(quantity_milli>0),
  PRIMARY KEY(order_id,ingredient_id));
CREATE TABLE IF NOT EXISTS stock_movements (
  id INTEGER PRIMARY KEY, branch_id INTEGER NOT NULL,
  ingredient_id INTEGER NOT NULL REFERENCES ingredients(id),
  quantity_milli INTEGER NOT NULL, reason TEXT NOT NULL, reference TEXT NOT NULL DEFAULT '',
  created_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS suppliers (
  id INTEGER PRIMARY KEY, branch_id INTEGER NOT NULL, name TEXT NOT NULL,
  phone TEXT NOT NULL DEFAULT '', email TEXT NOT NULL DEFAULT '', active INTEGER NOT NULL DEFAULT 1);
CREATE TABLE IF NOT EXISTS purchases (
  id INTEGER PRIMARY KEY, branch_id INTEGER NOT NULL, supplier_id INTEGER NOT NULL REFERENCES suppliers(id),
  ingredient_id INTEGER NOT NULL REFERENCES ingredients(id), quantity_milli INTEGER NOT NULL,
  amount_cents INTEGER NOT NULL, note TEXT NOT NULL DEFAULT '', created_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS expenses (
  id INTEGER PRIMARY KEY, branch_id INTEGER NOT NULL, user_id INTEGER NOT NULL,
  category TEXT NOT NULL, description TEXT NOT NULL, method TEXT NOT NULL,
  amount_cents INTEGER NOT NULL CHECK(amount_cents>0), created_at TEXT NOT NULL);
CREATE INDEX IF NOT EXISTS ix_expenses_branch_date ON expenses(branch_id,created_at);
CREATE TABLE IF NOT EXISTS customers (
  id INTEGER PRIMARY KEY, branch_id INTEGER NOT NULL, name TEXT NOT NULL,
  phone TEXT NOT NULL, email TEXT NOT NULL DEFAULT '', address TEXT NOT NULL DEFAULT '',
  created_at TEXT NOT NULL, UNIQUE(branch_id,phone));
CREATE TABLE IF NOT EXISTS loyalty_ledger (
  id INTEGER PRIMARY KEY, branch_id INTEGER NOT NULL,
  customer_id INTEGER NOT NULL REFERENCES customers(id), order_id INTEGER REFERENCES orders(id),
  points INTEGER NOT NULL, reason TEXT NOT NULL, created_at TEXT NOT NULL);
CREATE INDEX IF NOT EXISTS ix_loyalty_customer ON loyalty_ledger(customer_id);
CREATE TABLE IF NOT EXISTS deliveries (
  id INTEGER PRIMARY KEY, branch_id INTEGER NOT NULL, order_id INTEGER NOT NULL UNIQUE REFERENCES orders(id),
  address TEXT NOT NULL, phone TEXT NOT NULL, driver TEXT NOT NULL DEFAULT '',
  status TEXT NOT NULL DEFAULT 'pending', updated_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS change_events (
  id INTEGER PRIMARY KEY AUTOINCREMENT, branch_id INTEGER NOT NULL, created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP);
CREATE INDEX IF NOT EXISTS ix_events_branch ON change_events(branch_id,id);
"""


def upgrade():
    with database() as db:
        db.execute('BEGIN IMMEDIATE')
        additions = {
            'users': {'branch_id':'INTEGER NOT NULL DEFAULT 1'},
            'sessions': {'branch_id':'INTEGER'},
            'menu_items': {'branch_id':'INTEGER NOT NULL DEFAULT 1'},
            'dining_tables': {'branch_id':'INTEGER NOT NULL DEFAULT 1','x':'INTEGER NOT NULL DEFAULT 0',
                              'y':'INTEGER NOT NULL DEFAULT 0','qr_token':"TEXT NOT NULL DEFAULT ''"},
            'orders': {'branch_id':'INTEGER NOT NULL DEFAULT 1','client_key':'TEXT',
                       'customer_id':'INTEGER','cogs_cents':'INTEGER NOT NULL DEFAULT 0',
                       'loyalty_used':'INTEGER NOT NULL DEFAULT 0'},
            'payments': {'recorded_by':'INTEGER'},
        }
        additions['refunds']={'cogs_reversal_cents':'INTEGER NOT NULL DEFAULT 0'} if db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='refunds'").fetchone() else {}
        for table, fields in additions.items():
            existing={r['name'] for r in db.execute(f'PRAGMA table_info({table})')}
            for field,decl in fields.items():
                if field not in existing:
                    db.execute(f'ALTER TABLE {table} ADD COLUMN {field} {decl}')
        db.executescript(SCHEMA)
        db.execute('''INSERT OR IGNORE INTO branches(id,name,code,public_token,created_at)
             VALUES(1,'Main branch','MAIN',?,?)''',(secrets.token_urlsafe(22),utcnow()))
        db.execute("CREATE UNIQUE INDEX IF NOT EXISTS ux_order_client_key ON orders(branch_id,client_key) WHERE client_key IS NOT NULL")
        for table in db.execute("SELECT id FROM dining_tables WHERE qr_token='' OR qr_token IS NULL"):
            db.execute('UPDATE dining_tables SET qr_token=? WHERE id=?',(secrets.token_urlsafe(24),table['id']))
        # Triggers are small commit markers used by the kitchen's EventSource.
        for table in ('orders','order_items','payments','dining_tables','refunds','ingredients','expenses'):
            branch_expr={
                'orders':'NEW.branch_id','order_items':'(SELECT branch_id FROM orders WHERE id=NEW.order_id)',
                'payments':'(SELECT branch_id FROM orders WHERE id=NEW.order_id)',
                'dining_tables':'NEW.branch_id','refunds':'NEW.branch_id',
                'ingredients':'NEW.branch_id','expenses':'NEW.branch_id'}[table]
            for action in ('INSERT','UPDATE'):
                db.execute(f'''CREATE TRIGGER IF NOT EXISTS ev_{table}_{action.lower()}
                   AFTER {action} ON {table} BEGIN
                   INSERT INTO change_events(branch_id) VALUES({branch_expr}); END''')
        db.commit()


def log(db, user, action, entity, entity_id, detail=''):
    db.execute('''INSERT INTO audits(branch_id,user_id,actor,action,entity,entity_id,detail,created_at)
                  VALUES(?,?,?,?,?,?,?,?)''',
               (user['branch_id'],user['id'],user['name'],action,entity,str(entity_id),str(detail)[:600],utcnow()))


def clean(v, field, limit=120):
    return require_text(v,field,limit)


def branch_order(db, oid, user):
    return get_order(db,oid,user['branch_id'])


def loyalty_balance(db,customer_id):
    return db.execute('SELECT COALESCE(SUM(points),0) n FROM loyalty_ledger WHERE customer_id=?',(customer_id,)).fetchone()['n']


class ShiftOpen(BaseModel):
    opening_cash_cents: int = Field(ge=0,le=100000000)


class ShiftClose(BaseModel):
    closing_cash_cents: int = Field(ge=0,le=100000000)
    note: str = ''


class Refund(BaseModel):
    amount_cents: int = Field(gt=0,le=100000000)
    method: Literal['cash','card','other']='cash'
    reason: str
    restock: bool=False


class TableTransfer(BaseModel):
    table_id: int


class Ingredient(BaseModel):
    id:int|None=None
    name:str
    unit:Literal['g','ml','pcs']='g'
    stock_milli:int=Field(ge=0,le=1000000000)
    low_stock_milli:int=Field(ge=0,le=1000000000)
    cost_cents:int=Field(ge=0,le=100000000)
    active:bool=True


class Recipe(BaseModel):
    ingredient_id:int
    quantity_milli:int=Field(gt=0,le=100000000)


class Supplier(BaseModel):
    name:str
    phone:str=''
    email:str=''


class Purchase(BaseModel):
    supplier_id:int
    ingredient_id:int
    quantity_milli:int=Field(gt=0,le=1000000000)
    amount_cents:int=Field(ge=0,le=100000000)
    note:str=''


class Waste(BaseModel):
    ingredient_id:int
    quantity_milli:int=Field(gt=0,le=1000000000)
    reason:str


class Expense(BaseModel):
    category:str
    description:str
    amount_cents:int=Field(gt=0,le=100000000)
    method:Literal['cash','card','bank','other']='cash'


class Customer(BaseModel):
    id:int|None=None
    name:str
    phone:str
    email:str=''
    address:str=''


class LoyaltyRedeem(BaseModel):
    points: int=Field(gt=0,le=100000)


class DeliveryUpdate(BaseModel):
    status:Literal['pending','assigned','picked_up','delivered','failed']
    driver:str=''
    address:str=''
    phone:str=''


class BranchCreate(BaseModel):
    name:str
    code:str
    clone_menu:bool=True


class BranchSwitch(BaseModel):
    branch_id:int


class WebOrder(BaseModel):
    token:str
    service_type:Literal['dine_in','takeaway','delivery']='takeaway'
    items:list[OrderLine]=Field(min_length=1,max_length=30)
    customer_name:str
    phone:str=''
    address:str=''
    note:str=''
    client_key:str=Field(min_length=8,max_length=80)


def install(app):
    upgrade()

    @app.get('/api/pro/bootstrap')
    def pro_bootstrap(request:Request):
        with database() as db:
            u=auth(request,db)
            branch=u['branch_id']
            own=db.execute('SELECT * FROM shifts WHERE user_id=? AND closed_at IS NULL',(u['id'],)).fetchone()
            base={'branch_id':branch,'current_shift':dict(own) if own else None,
                  'branches':[dict(r) for r in db.execute('SELECT id,name,code,active FROM branches ORDER BY id')] if u['role']=='admin' else [],
                  'ingredients':[dict(r) for r in db.execute('SELECT * FROM ingredients WHERE branch_id=? ORDER BY name',(branch,))] if u['role']=='admin' else [],
                  'recipes':[dict(r) for r in db.execute('''SELECT r.*,m.name item_name,i.name ingredient_name,i.unit,
                             i.cost_cents FROM recipes r JOIN menu_items m ON m.id=r.menu_item_id
                             JOIN ingredients i ON i.id=r.ingredient_id WHERE m.branch_id=? ORDER BY m.name''',(branch,))] if u['role']=='admin' else [],
                  'suppliers':[dict(r) for r in db.execute('SELECT * FROM suppliers WHERE branch_id=? ORDER BY name',(branch,))] if u['role']=='admin' else [],
                  'expenses':[dict(r) for r in db.execute('SELECT * FROM expenses WHERE branch_id=? ORDER BY id DESC LIMIT 100',(branch,))] if u['role']=='admin' else [],
                  'purchases':[dict(r) for r in db.execute('''SELECT p.*,i.name ingredient,s.name supplier FROM purchases p
                             JOIN ingredients i ON i.id=p.ingredient_id JOIN suppliers s ON s.id=p.supplier_id
                             WHERE p.branch_id=? ORDER BY p.id DESC LIMIT 100''',(branch,))] if u['role']=='admin' else [],
                  'customers':[dict(r)|{'points':loyalty_balance(db,r['id']),
                       'paid_orders':db.execute("SELECT COUNT(*) n FROM orders WHERE customer_id=? AND status='paid'",(r['id'],)).fetchone()['n'],
                       'lifetime_spend_cents':db.execute("SELECT COALESCE(SUM(total_cents),0) n FROM orders WHERE customer_id=? AND status='paid'",(r['id'],)).fetchone()['n']}
                       for r in db.execute('SELECT * FROM customers WHERE branch_id=? ORDER BY id DESC LIMIT 300',(branch,))] if u['role']!='kitchen' else [],
                  'deliveries':[dict(r) for r in db.execute('''SELECT d.*,o.order_number,o.total_cents,o.customer_name
                                FROM deliveries d JOIN orders o ON o.id=d.order_id
                                WHERE d.branch_id=? ORDER BY d.id DESC LIMIT 100''',(branch,))] if u['role']!='kitchen' else [],
                  'recent_audits':[dict(r) for r in db.execute('SELECT * FROM audits WHERE branch_id=? ORDER BY id DESC LIMIT 120',(branch,))] if u['role']=='admin' else [],
                  'shifts':[dict(r) for r in db.execute('''SELECT s.*,u.name user_name FROM shifts s
                             JOIN users u ON u.id=s.user_id WHERE s.branch_id=? ORDER BY s.id DESC LIMIT 100''',(branch,))] if u['role']=='admin' else [],
                 }
            return base

    @app.get('/api/events')
    async def events(request:Request):
        with database() as db:
            user=auth(request,db)
            branch=user['branch_id']
        async def stream():
            with database() as db:
                row=db.execute('SELECT COALESCE(MAX(id),0) n FROM change_events WHERE branch_id=?',(branch,)).fetchone()
                last=row['n']
            yield ': connected\n\n'
            while True:
                if await request.is_disconnected(): break
                try:
                    with database() as db:
                        latest=db.execute('SELECT COALESCE(MAX(id),0) n FROM change_events WHERE branch_id=?',(branch,)).fetchone()['n']
                    if latest>last:
                        last=latest
                        yield f'event: sync\ndata: {json.dumps({"version":last})}\n\n'
                    else:
                        yield ': ping\n\n'
                except sqlite3.DatabaseError:
                    yield ': temporary database issue\n\n'
                await asyncio.sleep(2)
        return StreamingResponse(stream(),media_type='text/event-stream',headers={'Cache-Control':'no-cache, no-transform','X-Accel-Buffering':'no'})

    @app.post('/api/pro/shifts/open')
    def shift_open(payload:ShiftOpen,request:Request):
        with database() as db:
            u=auth(request,db,['admin','cashier'],True)
            db.execute('BEGIN IMMEDIATE')
            if db.execute('SELECT 1 FROM shifts WHERE user_id=? AND closed_at IS NULL',(u['id'],)).fetchone():
                raise HTTPException(409,'You already have an open shift')
            cursor=db.execute('INSERT INTO shifts(branch_id,user_id,opening_cash_cents,opened_at) VALUES(?,?,?,?)',
                              (u['branch_id'],u['id'],payload.opening_cash_cents,utcnow()))
            log(db,u,'shift_open','shift',cursor.lastrowid,f'Opening cash {payload.opening_cash_cents}')
            db.commit()
            return {'ok':True,'shift_id':cursor.lastrowid}

    @app.post('/api/pro/shifts/{sid}/close')
    def shift_close(sid:int,payload:ShiftClose,request:Request):
        with database() as db:
            u=auth(request,db,['admin','cashier'],True)
            db.execute('BEGIN IMMEDIATE')
            row=db.execute('SELECT * FROM shifts WHERE id=? AND branch_id=? AND user_id=? AND closed_at IS NULL',
                           (sid,u['branch_id'],u['id'])).fetchone()
            if not row:raise HTTPException(404,'Open shift not found')
            payments=db.execute('''SELECT COALESCE(SUM(p.amount_cents),0) n FROM payments p
                    JOIN orders o ON o.id=p.order_id
                    WHERE p.recorded_by=? AND p.method='cash' AND o.branch_id=?
                    AND p.created_at>=?''',(u['id'],u['branch_id'],row['opened_at'])).fetchone()['n']
            refunds=db.execute('''SELECT COALESCE(SUM(amount_cents),0) n FROM refunds WHERE user_id=?
                  AND method='cash' AND branch_id=? AND created_at>=?''',
                  (u['id'],u['branch_id'],row['opened_at'])).fetchone()['n']
            expenses=db.execute('''SELECT COALESCE(SUM(amount_cents),0) n FROM expenses WHERE user_id=?
                  AND method='cash' AND branch_id=? AND created_at>=?''',
                  (u['id'],u['branch_id'],row['opened_at'])).fetchone()['n']
            expected=row['opening_cash_cents']+payments-refunds-expenses
            variance=payload.closing_cash_cents-expected
            db.execute('''UPDATE shifts SET closing_cash_cents=?,expected_cash_cents=?,variance_cents=?,
                         closed_at=?,note=? WHERE id=?''',
                       (payload.closing_cash_cents,expected,variance,utcnow(),payload.note.strip()[:300],sid))
            log(db,u,'shift_close','shift',sid,f'Expected {expected}, counted {payload.closing_cash_cents}, variance {variance}')
            db.commit()
            return {'ok':True,'expected_cents':expected,'variance_cents':variance}

    @app.post('/api/pro/orders/{oid}/transfer')
    def transfer_table(oid:int,payload:TableTransfer,request:Request):
        with database() as db:
            u=auth(request,db,['admin','cashier'],True)
            db.execute('BEGIN IMMEDIATE')
            o=branch_order(db,oid,u)
            if o['service_type']!='dine_in' or o['status'] in ('paid','void'):raise HTTPException(409,'Only open dine-in orders can be transferred')
            t=db.execute('SELECT id FROM dining_tables WHERE id=? AND branch_id=? AND active=1',(payload.table_id,u['branch_id'])).fetchone()
            if not t:raise HTTPException(404,'Table not found')
            busy=db.execute("SELECT id FROM orders WHERE branch_id=? AND table_id=? AND status NOT IN ('paid','void') AND id!=?",
                            (u['branch_id'],payload.table_id,oid)).fetchone()
            if busy:raise HTTPException(409,'Destination table occupied')
            db.execute('UPDATE orders SET table_id=? WHERE id=?',(payload.table_id,oid))
            log(db,u,'table_transfer','order',oid,f'{o["table_id"]} → {payload.table_id}')
            db.commit()
            return {'ok':True}

    @app.post('/api/pro/orders/{oid}/refund')
    def refund_order(oid:int,payload:Refund,request:Request):
        with database() as db:
            u=auth(request,db,['admin'],True)
            reason=clean(payload.reason,'Refund reason',280)
            db.execute('BEGIN IMMEDIATE')
            o=branch_order(db,oid,u)
            if o['status']!='paid':raise HTTPException(409,'Only paid orders can be refunded')
            existing=db.execute('SELECT COALESCE(SUM(amount_cents),0) n FROM refunds WHERE order_id=?',(oid,)).fetchone()['n']
            if payload.amount_cents+existing>o['total_cents']: raise HTTPException(422,'Refund exceeds unrefunded order total')
            if payload.restock and existing+payload.amount_cents!=o['total_cents']:
                raise HTTPException(422,'Restocking is allowed only on the final full refund')
            if payload.restock:
                for r in db.execute('''SELECT menu_item_id,SUM(quantity) qty FROM order_items WHERE order_id=? GROUP BY menu_item_id''',(oid,)):
                    db.execute('UPDATE menu_items SET stock=stock+? WHERE id=? AND track_stock=1',(r['qty'],r['menu_item_id']))
                # Recipe ingredients also get restocked only on full order refund.
                for r in db.execute('SELECT ingredient_id,quantity_milli qty FROM order_stock_usage WHERE order_id=?',(oid,)):
                    db.execute('UPDATE ingredients SET stock_milli=stock_milli+? WHERE id=?',(r['qty'],r['ingredient_id']))
                    db.execute('INSERT INTO stock_movements(branch_id,ingredient_id,quantity_milli,reason,reference,created_at) VALUES(?,?,?,?,?,?)',
                               (u['branch_id'],r['ingredient_id'],r['qty'],'refund_restock',str(oid),utcnow()))
            reversal=o['cogs_cents'] if payload.restock else 0
            cur=db.execute('''INSERT INTO refunds(order_id,branch_id,user_id,method,amount_cents,reason,restock,cogs_reversal_cents,created_at)
                         VALUES(?,?,?,?,?,?,?,?,?)''',
                         (oid,u['branch_id'],u['id'],payload.method,payload.amount_cents,reason,int(payload.restock),reversal,utcnow()))
            if o['customer_id'] and existing+payload.amount_cents==o['total_cents']:
                earned=db.execute("SELECT COALESCE(SUM(points),0) n FROM loyalty_ledger WHERE order_id=? AND reason='earn'",(oid,)).fetchone()['n']
                if earned:
                    db.execute('INSERT INTO loyalty_ledger(branch_id,customer_id,order_id,points,reason,created_at) VALUES(?,?,?,?,?,?)',
                               (u['branch_id'],o['customer_id'],oid,-earned,'refund_reversal',utcnow()))
            log(db,u,'refund','order',oid,f'{payload.amount_cents} {payload.method}: {reason}')
            db.commit()
            return {'ok':True,'refund_id':cur.lastrowid,'total_refunded_cents':existing+payload.amount_cents}

    @app.get('/api/pro/orders/{oid}/kot')
    def kitchen_ticket(oid:int,request:Request):
        with database() as db:
            u=auth(request,db)
            o=branch_order(db,oid,u)
            return {'order':o,'issued_at':utcnow(),'heading':'KITCHEN ORDER TICKET'}

    @app.post('/api/pro/ingredients')
    def ingredient_save(p:Ingredient,request:Request):
        with database() as db:
            u=auth(request,db,['admin'],True)
            db.execute('BEGIN IMMEDIATE')
            if p.id:
                found=db.execute('SELECT 1 FROM ingredients WHERE id=? AND branch_id=?',(p.id,u['branch_id'])).fetchone()
                if not found:raise HTTPException(404,'Ingredient not found')
                db.execute('''UPDATE ingredients SET name=?,unit=?,stock_milli=?,low_stock_milli=?,cost_cents=?,active=? WHERE id=?''',
                           (clean(p.name,'Ingredient'),p.unit,p.stock_milli,p.low_stock_milli,p.cost_cents,int(p.active),p.id))
                iid=p.id
            else:
                c=db.execute('''INSERT INTO ingredients(branch_id,name,unit,stock_milli,low_stock_milli,cost_cents,active)
                     VALUES(?,?,?,?,?,?,?)''',(u['branch_id'],clean(p.name,'Ingredient'),p.unit,p.stock_milli,p.low_stock_milli,p.cost_cents,int(p.active)))
                iid=c.lastrowid
            log(db,u,'ingredient_save','ingredient',iid)
            db.commit()
            return {'ok':True,'id':iid}

    @app.post('/api/pro/recipes/{menu_id}')
    def recipe_upsert(menu_id:int,p:Recipe,request:Request):
        with database() as db:
            u=auth(request,db,['admin'],True)
            db.execute('BEGIN IMMEDIATE')
            if not db.execute('SELECT 1 FROM menu_items WHERE id=? AND branch_id=?',(menu_id,u['branch_id'])).fetchone():raise HTTPException(404,'Menu item not found')
            if not db.execute('SELECT 1 FROM ingredients WHERE id=? AND branch_id=?',(p.ingredient_id,u['branch_id'])).fetchone():raise HTTPException(404,'Ingredient not found')
            db.execute('''INSERT INTO recipes(menu_item_id,ingredient_id,quantity_milli) VALUES(?,?,?)
                         ON CONFLICT(menu_item_id,ingredient_id) DO UPDATE SET quantity_milli=excluded.quantity_milli''',
                       (menu_id,p.ingredient_id,p.quantity_milli))
            log(db,u,'recipe_save','menu',menu_id,f'Ingredient {p.ingredient_id}; qty {p.quantity_milli}')
            db.commit()
            return {'ok':True}

    @app.delete('/api/pro/recipes/{menu_id}/{ingredient_id}')
    def recipe_remove(menu_id:int,ingredient_id:int,request:Request):
        with database() as db:
            u=auth(request,db,['admin'],True)
            if not db.execute('SELECT 1 FROM menu_items WHERE id=? AND branch_id=?',(menu_id,u['branch_id'])).fetchone():raise HTTPException(404,'Menu not found')
            db.execute('DELETE FROM recipes WHERE menu_item_id=? AND ingredient_id=?',(menu_id,ingredient_id))
            log(db,u,'recipe_remove','menu',menu_id,str(ingredient_id))
            return {'ok':True}

    @app.post('/api/pro/suppliers')
    def supplier_add(p:Supplier,request:Request):
        with database() as db:
            u=auth(request,db,['admin'],True)
            c=db.execute('INSERT INTO suppliers(branch_id,name,phone,email) VALUES(?,?,?,?)',
                         (u['branch_id'],clean(p.name,'Supplier'),p.phone.strip()[:40],p.email.strip()[:120]))
            log(db,u,'supplier_create','supplier',c.lastrowid)
            return {'ok':True}

    @app.post('/api/pro/purchases')
    def purchase_receive(p:Purchase,request:Request):
        with database() as db:
            u=auth(request,db,['admin'],True)
            db.execute('BEGIN IMMEDIATE')
            if not db.execute('SELECT 1 FROM suppliers WHERE id=? AND branch_id=?',(p.supplier_id,u['branch_id'])).fetchone():raise HTTPException(404,'Supplier not found')
            if not db.execute('SELECT 1 FROM ingredients WHERE id=? AND branch_id=?',(p.ingredient_id,u['branch_id'])).fetchone():raise HTTPException(404,'Ingredient not found')
            cur=db.execute('''INSERT INTO purchases(branch_id,supplier_id,ingredient_id,quantity_milli,amount_cents,note,created_at)
                             VALUES(?,?,?,?,?,?,?)''',(u['branch_id'],p.supplier_id,p.ingredient_id,p.quantity_milli,p.amount_cents,p.note.strip()[:300],utcnow()))
            db.execute('''UPDATE ingredients SET stock_milli=stock_milli+?,
                     cost_cents=CASE WHEN ?>0 THEN CAST(ROUND(?*1000.0/?) AS INTEGER) ELSE cost_cents END
                     WHERE id=?''',(p.quantity_milli,p.amount_cents,p.amount_cents,p.quantity_milli,p.ingredient_id))
            db.execute('''INSERT INTO stock_movements(branch_id,ingredient_id,quantity_milli,reason,reference,created_at)
                          VALUES(?,?,?,?,?,?)''',(u['branch_id'],p.ingredient_id,p.quantity_milli,'purchase',str(cur.lastrowid),utcnow()))
            log(db,u,'purchase_received','purchase',cur.lastrowid,f'{p.quantity_milli} stock; {p.amount_cents} cost')
            db.commit()
            return {'ok':True,'purchase_id':cur.lastrowid}

    @app.post('/api/pro/waste')
    def waste(p:Waste,request:Request):
        with database() as db:
            u=auth(request,db,['admin'],True)
            reason=clean(p.reason,'Waste reason',160)
            db.execute('BEGIN IMMEDIATE')
            cur=db.execute('UPDATE ingredients SET stock_milli=stock_milli-? WHERE id=? AND branch_id=? AND stock_milli>=?',
                           (p.quantity_milli,p.ingredient_id,u['branch_id'],p.quantity_milli))
            if cur.rowcount!=1:raise HTTPException(409,'Not enough ingredient stock')
            db.execute('INSERT INTO stock_movements(branch_id,ingredient_id,quantity_milli,reason,reference,created_at) VALUES(?,?,?,?,?,?)',
                       (u['branch_id'],p.ingredient_id,-p.quantity_milli,'waste',reason,utcnow()))
            log(db,u,'inventory_waste','ingredient',p.ingredient_id,reason)
            db.commit()
            return {'ok':True}

    @app.post('/api/pro/expenses')
    def expense_add(p:Expense,request:Request):
        with database() as db:
            u=auth(request,db,['admin','cashier'],True)
            c=db.execute('''INSERT INTO expenses(branch_id,user_id,category,description,method,amount_cents,created_at)
                           VALUES(?,?,?,?,?,?,?)''',(u['branch_id'],u['id'],clean(p.category,'Category',60),clean(p.description,'Description',250),p.method,p.amount_cents,utcnow()))
            log(db,u,'expense_create','expense',c.lastrowid,f'{p.amount_cents} {p.method}')
            return {'ok':True}

    @app.post('/api/pro/customers')
    def customer_save(p:Customer,request:Request):
        with database() as db:
            u=auth(request,db,['admin','cashier'],True)
            name=clean(p.name,'Customer name')
            phone=clean(p.phone,'Phone',40)
            try:
                if p.id:
                    cur=db.execute('UPDATE customers SET name=?,phone=?,email=?,address=? WHERE id=? AND branch_id=?',
                                   (name,phone,p.email.strip()[:120],p.address.strip()[:300],p.id,u['branch_id']))
                    if cur.rowcount!=1:raise HTTPException(404,'Customer not found')
                    cid=p.id
                else:
                    cur=db.execute('INSERT INTO customers(branch_id,name,phone,email,address,created_at) VALUES(?,?,?,?,?,?)',
                                   (u['branch_id'],name,phone,p.email.strip()[:120],p.address.strip()[:300],utcnow()))
                    cid=cur.lastrowid
            except sqlite3.IntegrityError:raise HTTPException(409,'Phone already registered at this branch')
            log(db,u,'customer_save','customer',cid)
            return {'ok':True,'customer_id':cid}

    @app.post('/api/pro/orders/{oid}/loyalty/redeem')
    def redeem_points(oid:int,p:LoyaltyRedeem,request:Request):
        # 1 point = one minor currency unit * 100. Redeemed before payment.
        with database() as db:
            u=auth(request,db,['admin','cashier'],True)
            db.execute('BEGIN IMMEDIATE')
            order=branch_order(db,oid,u)
            if order['status'] in ('paid','void') or not order['customer_id']:raise HTTPException(409,'Open order with linked customer required')
            balance=loyalty_balance(db,order['customer_id'])
            if balance<p.points:raise HTTPException(422,'Not enough loyalty points')
            if order['loyalty_used']:raise HTTPException(409,'Points already redeemed on this order')
            discount=p.points*100
            if discount>=order['total_cents']:raise HTTPException(422,'Points cannot cover the full order amount')
            new_discount=order['discount_cents']+discount
            rate=float(get_setting(db,'tax_percent','0'))
            new_tax=round((order['subtotal_cents']-new_discount)*rate/100)
            new_total=order['subtotal_cents']-new_discount+new_tax
            db.execute('UPDATE orders SET loyalty_used=?,discount_cents=?,tax_cents=?,total_cents=? WHERE id=?',
                       (p.points,new_discount,new_tax,new_total,oid))
            db.execute('INSERT INTO loyalty_ledger(branch_id,customer_id,order_id,points,reason,created_at) VALUES(?,?,?,?,?,?)',
                       (u['branch_id'],order['customer_id'],oid,-p.points,'redeem',utcnow()))
            log(db,u,'loyalty_redeem','order',oid,str(p.points))
            db.commit()
            return {'ok':True,'order':get_order(db,oid,u['branch_id'])}

    @app.post('/api/pro/deliveries/{oid}')
    def delivery_update(oid:int,p:DeliveryUpdate,request:Request):
        with database() as db:
            u=auth(request,db,['admin','cashier'],True)
            o=branch_order(db,oid,u)
            if o['service_type']!='delivery':raise HTTPException(422,'Not a delivery order')
            original=db.execute('SELECT * FROM deliveries WHERE branch_id=? AND order_id=?',(u['branch_id'],oid)).fetchone()
            address=p.address.strip()[:300] or (original['address'] if original else '')
            phone=p.phone.strip()[:40] or (original['phone'] if original else '')
            if p.status in ('assigned','picked_up','delivered') and (not address or not phone):
                raise HTTPException(422,'Address and phone required before dispatch')
            db.execute('''INSERT INTO deliveries(branch_id,order_id,address,phone,driver,status,updated_at)
                    VALUES(?,?,?,?,?,?,?) ON CONFLICT(order_id) DO UPDATE SET driver=excluded.driver,
                    status=excluded.status,address=excluded.address,phone=excluded.phone,
                    updated_at=excluded.updated_at''',
                    (u['branch_id'],oid,address or 'Details to confirm',phone,p.driver.strip()[:120],p.status,utcnow()))
            log(db,u,'delivery_status','order',oid,p.status)
            return {'ok':True}

    @app.post('/api/pro/branches')
    def branch_create(p:BranchCreate,request:Request):
        with database() as db:
            u=auth(request,db,['admin'],True)
            code=clean(p.code,'Branch code',12).upper()
            if not re.fullmatch(r'[A-Z0-9_-]{2,12}',code):raise HTTPException(422,'Branch code: 2–12 letters/numbers/underscores')
            db.execute('BEGIN IMMEDIATE')
            try:
                cur=db.execute('INSERT INTO branches(name,code,public_token,created_at) VALUES(?,?,?,?)',
                               (clean(p.name,'Branch name'),code,secrets.token_urlsafe(24),utcnow()))
            except sqlite3.IntegrityError:raise HTTPException(409,'Branch code already taken')
            bid=cur.lastrowid
            for n in range(1,9):
                db.execute('''INSERT INTO dining_tables(label,zone,seats,active,branch_id,qr_token)
                  VALUES(?,?,4,1,?,?)''',(f'{code}-T{n:02}','Main dining',bid,secrets.token_urlsafe(24)))
            if p.clone_menu:
                db.execute('''INSERT INTO menu_items(name,category,price_cents,sku,stock,track_stock,active,created_at,branch_id)
                     SELECT name,category,price_cents,sku,0,0,active,?,? FROM menu_items WHERE branch_id=?''',
                     (utcnow(),bid,u['branch_id']))
            log(db,u,'branch_create','branch',bid,code)
            db.commit()
            return {'ok':True,'branch_id':bid}

    @app.post('/api/pro/branches/switch')
    def branch_switch(p:BranchSwitch,request:Request):
        with database() as db:
            u=auth(request,db,['admin'],True)
            if not db.execute('SELECT 1 FROM branches WHERE id=? AND active=1',(p.branch_id,)).fetchone():raise HTTPException(404,'Branch not found')
            digest=__import__('hashlib').sha256(request.cookies.get('restaurant_pos_session','').encode()).hexdigest()
            db.execute('UPDATE sessions SET branch_id=? WHERE token_hash=?',(p.branch_id,digest))
            log(db,u,'branch_switch','branch',p.branch_id)
            return {'ok':True}

    @app.get('/api/pro/reports')
    def advanced_reports(request:Request,from_date:str='',to_date:str=''):
        with database() as db:
            u=auth(request,db,['admin','cashier'])
            today=datetime.now(timezone.utc).date().isoformat()
            from_date=from_date or today
            to_date=to_date or today
            try:
                datetime.strptime(from_date,'%Y-%m-%d')
                datetime.strptime(to_date,'%Y-%m-%d')
            except ValueError:raise HTTPException(422,'Use YYYY-MM-DD dates')
            if to_date<from_date:raise HTTPException(422,'Invalid date range')
            bid=u['branch_id']
            sales=db.execute('''SELECT COUNT(*) orders,COALESCE(SUM(total_cents),0) sales,
                 COALESCE(SUM(cogs_cents),0) cogs,COALESCE(SUM(tax_cents),0) taxes,
                 COALESCE(SUM(discount_cents),0) discounts FROM orders
                 WHERE branch_id=? AND status='paid' AND substr(paid_at,1,10) BETWEEN ? AND ?''',(bid,from_date,to_date)).fetchone()
            refunded=db.execute('''SELECT COALESCE(SUM(amount_cents),0) n,
                 COALESCE(SUM(cogs_reversal_cents),0) reversed_cogs FROM refunds
                 WHERE branch_id=? AND substr(created_at,1,10) BETWEEN ? AND ?''',(bid,from_date,to_date)).fetchone()
            refund=refunded['n']
            net_cogs=sales['cogs']-refunded['reversed_cogs']
            expense=db.execute('''SELECT COALESCE(SUM(amount_cents),0) n FROM expenses
                 WHERE branch_id=? AND substr(created_at,1,10) BETWEEN ? AND ?''',(bid,from_date,to_date)).fetchone()['n']
            tenders=[dict(r) for r in db.execute('''SELECT p.method,SUM(p.amount_cents) amount_cents FROM payments p
                JOIN orders o ON o.id=p.order_id WHERE o.branch_id=? AND o.status='paid'
                AND substr(o.paid_at,1,10) BETWEEN ? AND ? GROUP BY p.method''',(bid,from_date,to_date))]
            trend=[dict(r) for r in db.execute('''SELECT substr(paid_at,1,10) day,SUM(total_cents) sales_cents,
                  COUNT(*) orders FROM orders WHERE branch_id=? AND status='paid'
                  AND substr(paid_at,1,10) BETWEEN ? AND ? GROUP BY day ORDER BY day''',(bid,from_date,to_date))]
            top=[dict(r) for r in db.execute('''SELECT i.item_name name,SUM(i.quantity) quantity,
                  SUM(i.unit_price_cents*i.quantity) gross_cents FROM order_items i JOIN orders o ON o.id=i.order_id
                  WHERE o.branch_id=? AND o.status='paid' AND substr(o.paid_at,1,10) BETWEEN ? AND ?
                  GROUP BY i.item_name ORDER BY quantity DESC LIMIT 12''',(bid,from_date,to_date))]
            net=sales['sales']-refund
            return {'from_date':from_date,'to_date':to_date,'orders':sales['orders'],
                    'gross_sales_cents':sales['sales'],'refunds_cents':refund,'net_sales_cents':net,
                    'cogs_cents':net_cogs,'expenses_cents':expense,
                    'estimated_profit_cents':net-net_cogs-expense,
                    'tax_cents':sales['taxes'],'discounts_cents':sales['discounts'],
                    'tenders':tenders,'trend':trend,'top_items':top,
                    'profit_note':'Estimate uses sales, refund dates, recipe cost snapshots, reversed cost on restocked refunds and recorded expenses; excludes unrecorded overhead.'}

    @app.get('/api/pro/reports/export')
    def export_csv(request:Request,from_date:str='',to_date:str=''):
        u=None
        with database() as db:
            u=auth(request,db,['admin'])
            today=datetime.now(timezone.utc).date().isoformat()
            a,b=from_date or today,to_date or today
            try:datetime.strptime(a,'%Y-%m-%d');datetime.strptime(b,'%Y-%m-%d')
            except ValueError:raise HTTPException(422,'Invalid date')
            out=io.StringIO()
            w=csv.writer(out)
            w.writerow(['Order','Type','Paid at UTC','Status','Subtotal minor','Discount minor','Tax minor','Total minor','Cost minor','Refund minor'])
            for r in db.execute('''SELECT o.*,COALESCE((SELECT SUM(amount_cents) FROM refunds WHERE order_id=o.id),0) refunded
                             FROM orders o WHERE o.branch_id=? AND o.status='paid'
                             AND substr(o.paid_at,1,10) BETWEEN ? AND ? ORDER BY o.id''',(u['branch_id'],a,b)):
                w.writerow([r['order_number'],r['service_type'],r['paid_at'],r['status'],r['subtotal_cents'],
                            r['discount_cents'],r['tax_cents'],r['total_cents'],r['cogs_cents'],r['refunded']])
            return Response(out.getvalue(),media_type='text/csv; charset=utf-8',headers={
                            'Content-Disposition':f'attachment; filename="pos-orders-{a}-{b}.csv"'})

    @app.get('/api/pro/tables/qr')
    def qr_links(request:Request):
        with database() as db:
            u=auth(request,db,['admin'])
            return {'links':[{'id':r['id'],'label':r['label'],'url':f'/guest?t={r["qr_token"]}'}
                             for r in db.execute('SELECT * FROM dining_tables WHERE branch_id=? AND active=1',(u['branch_id'],))],
                    'online':f'/guest?store={db.execute("SELECT public_token FROM branches WHERE id=?",(u["branch_id"],)).fetchone()["public_token"]}'}

    @app.get('/api/pro/tables/{table_id}/qr.svg')
    def table_qr_image(table_id:int,request:Request):
        import qrcode
        from qrcode.image.svg import SvgPathImage
        with database() as db:
            u=auth(request,db,['admin'])
            row=db.execute('SELECT qr_token FROM dining_tables WHERE id=? AND branch_id=? AND active=1',
                           (table_id,u['branch_id'])).fetchone()
            if not row:raise HTTPException(404,'Table not found')
            # Generated on current host; change your installation's public base URL before printing.
            base=os.getenv('POS_PUBLIC_BASE_URL',str(request.base_url).rstrip('/'))
            link=f'{base}/guest?t={row["qr_token"]}'
            svg=qrcode.make(link,image_factory=SvgPathImage,box_size=7,border=3)
            io_data=io.BytesIO();svg.save(io_data)
            return Response(io_data.getvalue(),media_type='image/svg+xml',headers={'Cache-Control':'private, no-store'})

    @app.get('/guest')
    def guest_page():
        return FileResponse(ROOT/'static'/'guest.html')

    @app.get('/api/public/catalog')
    def public_catalog(t:str='',store:str=''):
        with database() as db:
            table=db.execute('''SELECT t.*,b.name branch FROM dining_tables t JOIN branches b ON b.id=t.branch_id
                          WHERE t.qr_token=? AND t.active=1 AND b.active=1''',(t,)).fetchone() if t else None
            if table:
                bid=table['branch_id'];label=table['label'];service='dine_in'
            else:
                b=db.execute('SELECT * FROM branches WHERE public_token=? AND active=1',(store,)).fetchone() if store else None
                if not b:raise HTTPException(404,'Ordering link invalid')
                bid=b['id'];label=b['name'];service='takeaway'
            menu=[dict(r) for r in db.execute('SELECT id,name,category,price_cents,track_stock,stock FROM menu_items WHERE branch_id=? AND active=1 ORDER BY category,name',(bid,))]
            return {'restaurant':get_setting(db,'restaurant','Restaurant'),'branch':label,'service':service,
                    'currency':get_setting(db,'currency','PKR'),'menu':menu,
                    'branding':__import__('branding').public_branding(db,bid)}

    public_locks={}
    lock=threading.RLock()

    @app.post('/api/public/order')
    def web_order(p:WebOrder,request:Request):
        # Public route requires high-entropy storefront/table token and applies per-token rate limit.
        with lock:
            now=time.monotonic()
            hits=[t for t in public_locks.get(p.token,[]) if now-t<60]
            if len(hits)>=10:raise HTTPException(429,'Too many orders. Try again later')
            hits.append(now);public_locks[p.token]=hits
            if len(public_locks)>10000:
                public_locks.clear()  # bounded demo limiter, not a distributed rate limiter
        with database() as db:
            db.execute('BEGIN IMMEDIATE')
            table=db.execute('SELECT * FROM dining_tables WHERE qr_token=? AND active=1',(p.token,)).fetchone()
            b=db.execute('SELECT * FROM branches WHERE public_token=? AND active=1',(p.token,)).fetchone()
            if table:
                bid=table['branch_id'];type_='dine_in';table_id=table['id']
            elif b:
                bid=b['id'];type_=p.service_type if p.service_type in ('takeaway','delivery') else 'takeaway';table_id=None
            else:raise HTTPException(404,'Invalid or inactive ordering link')
            if not db.execute('SELECT id FROM branches WHERE id=? AND active=1',(bid,)).fetchone(): raise HTTPException(404,'Branch unavailable')
            existing=db.execute('SELECT id FROM orders WHERE branch_id=? AND client_key=?',(bid,p.client_key)).fetchone()
            if existing:return {'ok':True,'order_number':db.execute('SELECT order_number FROM orders WHERE id=?',(existing['id'],)).fetchone()['order_number'],'duplicate':True}
            if type_=='dine_in':
                if db.execute("SELECT 1 FROM orders WHERE table_id=? AND status NOT IN ('paid','void')",(table_id,)).fetchone():
                    raise HTTPException(409,'Table already has an active order. Please ask your waiter.')
            name=clean(p.customer_name,'Your name',100)
            if type_=='delivery' and (not p.address.strip() or not p.phone.strip()):
                raise HTTPException(422,'Delivery requires phone and address')
            lines,subtotal,discount,tax,total=calc_order(db,p.items,0,bid)
            if total<=0:raise HTTPException(422,'Total must be positive')
            admin=db.execute('SELECT id FROM users WHERE role="admin" AND active=1 ORDER BY id LIMIT 1').fetchone()
            if not admin:raise HTTPException(409,'Online orders are not ready')
            c=db.execute('''INSERT INTO orders(order_number,service_type,table_id,customer_name,note,status,
                 kitchen_status,subtotal_cents,discount_cents,tax_cents,total_cents,created_by,created_at,
                 branch_id,client_key) VALUES(?,?,?,?,?,'new','new',?,?,?,?,?,?,?,?)''',
                 ('TMP-'+secrets.token_hex(10),type_,table_id,name,p.note.strip()[:450],subtotal,discount,tax,total,admin['id'],utcnow(),bid,p.client_key))
            oid=c.lastrowid
            number=f'WEB-{datetime.now(timezone.utc):%Y%m%d}-{oid:05d}'
            db.execute('UPDATE orders SET order_number=? WHERE id=?',(number,oid))
            for line in lines:
                db.execute('INSERT INTO order_items(order_id,menu_item_id,item_name,unit_price_cents,quantity,note) VALUES(?,?,?,?,?,?)',(oid,*line))
            if type_=='delivery':
                db.execute('INSERT INTO deliveries(branch_id,order_id,address,phone,status,updated_at) VALUES(?,?,?,?,?,?)',
                           (bid,oid,clean(p.address,'Address',300),clean(p.phone,'Phone',40),'pending',utcnow()))
            db.commit()
            return {'ok':True,'order_number':number,'message':'Order placed. Pay when the restaurant confirms your order.'}

    @app.get('/api/health/pro')
    def pro_health():return {'status':'ok','edition':'three-phase-reference','schema':'v2'}

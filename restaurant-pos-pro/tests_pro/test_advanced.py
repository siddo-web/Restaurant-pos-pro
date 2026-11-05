"""Independent integration tests for phase 1/2/3. Run in a separate process:
python -m unittest discover -s tests_pro -v
"""
import os
import tempfile
import unittest
from pathlib import Path
from fastapi.testclient import TestClient

TEMP=tempfile.TemporaryDirectory()
os.environ['POS_DATABASE']=str(Path(TEMP.name)/'advanced.db')
from app import app

class AdvancedFlow(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.owner=TestClient(app)
        cls.owner.__enter__()
        c=cls.owner
        r=c.post('/api/setup',json={'restaurant':'Fork & Flour','name':'Owner','username':'owner','password':'MySecurePassword123','currency':'PKR'})
        assert r.status_code==200,r.text
        cls.header={'X-CSRF-Token':c.get('/api/session').json()['csrf']}
        cls.menu=c.get('/api/bootstrap').json()['menu']
        cls.tables=c.get('/api/bootstrap').json()['tables']

    @classmethod
    def tearDownClass(cls):cls.owner.__exit__(None,None,None)

    def post(self,route,data=None,client=None,header=None):
        c=client or self.owner
        h=header if header is not None else self.header
        return c.post(route,headers=h,json=data if data is not None else {})

    def test_01_setup_and_qr(self):
        c=self.owner
        self.assertEqual(len(self.tables),12)
        links=c.get('/api/pro/tables/qr').json()['links']
        self.assertEqual(len(links),12)
        token=links[0]['url'].split('?t=')[1]
        self.assertGreater(len(token),25)
        r=c.get('/api/pro/tables/1/qr.svg')
        self.assertEqual(r.status_code,200,r.text[:100]);self.assertIn(b'<svg',r.content)
        catalog=c.get('/api/public/catalog?t='+token)
        self.assertEqual(catalog.status_code,200)
        self.assertEqual(catalog.json()['service'],'dine_in')
        type(self).token=token

    def test_02_register_recipe_supplier_purchase(self):
        ing=self.post('/api/pro/ingredients',{'name':'Burger patty','unit':'pcs','stock_milli':0,'low_stock_milli':1000,'cost_cents':0})
        self.assertEqual(ing.status_code,200,ing.text)
        type(self).ingredient=ing.json()['id']
        supplier=self.post('/api/pro/suppliers',{'name':'Local Foods','phone':'03000000000'})
        self.assertEqual(supplier.status_code,200,supplier.text)
        sid=self.owner.get('/api/pro/bootstrap').json()['suppliers'][0]['id']
        purchase=self.post('/api/pro/purchases',{'supplier_id':sid,'ingredient_id':self.ingredient,'quantity_milli':10000,'amount_cents':50000})
        self.assertEqual(purchase.status_code,200,purchase.text)
        stock=self.owner.get('/api/pro/bootstrap').json()['ingredients'][0]
        self.assertEqual(stock['stock_milli'],10000)
        self.assertEqual(stock['cost_cents'],5000)
        m=next(x for x in self.menu if x['name']=='Classic Burger')
        type(self).burger=m
        recipe=self.post('/api/pro/recipes/'+str(m['id']),{'ingredient_id':self.ingredient,'quantity_milli':1000})
        self.assertEqual(recipe.status_code,200,recipe.text)

    def test_03_cashier_shifts_customer_and_checkout(self):
        self.assertEqual(self.post('/api/staff',{'name':'Cashier','username':'cashier','password':'MySecurePassword123','role':'cashier'}).status_code,200)
        type(self).cashier=TestClient(app)
        c=self.cashier
        self.assertEqual(c.post('/api/login',json={'username':'cashier','password':'MySecurePassword123'}).status_code,200)
        type(self).cashhdr={'X-CSRF-Token':c.get('/api/session').json()['csrf']}
        customer=self.post('/api/pro/customers',{'name':'Ali','phone':'03001234567'})
        self.assertEqual(customer.status_code,200,customer.text)
        type(self).customer_id=customer.json()['customer_id']
        order=self.post('/api/orders',{'service_type':'takeaway','customer_id':self.customer_id,
                                      'items':[{'item_id':self.burger['id'],'quantity':2}]},client=c,header=self.cashhdr)
        self.assertEqual(order.status_code,200,order.text)
        o=order.json()['order'];type(self).order=o
        self.assertEqual(self.post(f'/api/orders/{o["id"]}/checkout',{'payments':[{'method':'cash','amount_cents':o['total_cents']}]},client=c,header=self.cashhdr).status_code,409)
        shift=self.post('/api/pro/shifts/open',{'opening_cash_cents':100000},client=c,header=self.cashhdr)
        self.assertEqual(shift.status_code,200,shift.text)
        paid=self.post(f'/api/orders/{o["id"]}/checkout',{'payments':[{'method':'cash','amount_cents':o['total_cents']}]},client=c,header=self.cashhdr)
        self.assertEqual(paid.status_code,200,paid.text)
        self.assertEqual(paid.json()['order']['cogs_cents'],10000)
        self.assertEqual(self.owner.get('/api/pro/bootstrap').json()['ingredients'][0]['stock_milli'],8000)
        customer=next(x for x in self.owner.get('/api/pro/bootstrap').json()['customers'] if x['id']==self.customer_id)
        self.assertEqual(customer['points'],o['total_cents']//10000)
        end=self.post('/api/pro/shifts/'+str(shift.json()['shift_id'])+'/close',{'closing_cash_cents':100000+o['total_cents']},client=c,header=self.cashhdr)
        self.assertEqual(end.status_code,200,end.text)
        self.assertEqual(end.json()['variance_cents'],0)

    def test_04_refund_audit(self):
        paid=self.order
        bad=self.post(f'/api/pro/orders/{paid["id"]}/refund',{'amount_cents':paid['total_cents']+1,'method':'cash','reason':'Mistake'})
        self.assertEqual(bad.status_code,422)
        denied=self.post(f'/api/pro/orders/{paid["id"]}/refund',{'amount_cents':100,'method':'cash','reason':'Refund'},client=self.cashier,header=self.cashhdr)
        self.assertEqual(denied.status_code,403)
        refund=self.post(f'/api/pro/orders/{paid["id"]}/refund',{'amount_cents':paid['total_cents'],'method':'cash','reason':'Cancelled food','restock':True})
        self.assertEqual(refund.status_code,200,refund.text)
        self.assertEqual(self.owner.get('/api/pro/bootstrap').json()['ingredients'][0]['stock_milli'],10000)
        self.assertEqual(next(x for x in self.owner.get('/api/pro/bootstrap').json()['customers'] if x['id']==self.customer_id)['points'],0)
        self.assertEqual(self.post(f'/api/pro/orders/{paid["id"]}/refund',{'amount_cents':1,'reason':'Second'}).status_code,422)
        actions=[x['action'] for x in self.owner.get('/api/pro/bootstrap').json()['recent_audits']]
        self.assertIn('checkout',actions);self.assertIn('refund',actions);self.assertIn('shift_close',actions)

    def test_05_web_order_replay_and_table_block(self):
        public=TestClient(app)
        payload={'token':self.token,'service_type':'dine_in','client_key':'unique-qr-order-0001',
                 'customer_name':'Visiting Guest','items':[{'item_id':self.burger['id'],'quantity':1}]}
        r=public.post('/api/public/order',json=payload)
        self.assertEqual(r.status_code,200,r.text)
        replay=public.post('/api/public/order',json=payload)
        self.assertEqual(replay.status_code,200,replay.text);self.assertTrue(replay.json()['duplicate'])
        changed=payload.copy();changed['client_key']='unique-qr-order-0002'
        busy=public.post('/api/public/order',json=changed)
        self.assertEqual(busy.status_code,409,busy.text)

    def test_06_branch_isolation_and_analytics(self):
        prior_id=self.order['id']
        r=self.post('/api/pro/branches',{'name':'Sea View','code':'SEA','clone_menu':True})
        self.assertEqual(r.status_code,200,r.text)
        branch=r.json()['branch_id']
        self.assertEqual(self.post('/api/pro/branches/switch',{'branch_id':branch}).status_code,200)
        bootstrap=self.owner.get('/api/bootstrap').json()
        self.assertEqual(len(bootstrap['menu']),len(self.menu))
        self.assertEqual(len(bootstrap['tables']),8)
        self.assertEqual(len(bootstrap['orders']),0)
        self.assertEqual(self.owner.get(f'/api/orders/{prior_id}/receipt').status_code,404)
        self.assertEqual(self.post('/api/orders',{'service_type':'takeaway','items':[{'item_id':self.burger['id'],'quantity':1}]}).status_code,422)
        self.assertEqual(self.owner.get('/api/pro/reports').json()['net_sales_cents'],0)
        self.assertEqual(self.post('/api/pro/branches/switch',{'branch_id':1}).status_code,200)
        self.assertGreater(len(self.owner.get('/api/bootstrap').json()['orders']),0)
        self.assertEqual(self.owner.get(f'/api/orders/{prior_id}/receipt').status_code,200)
        report=self.owner.get('/api/pro/reports').json()
        self.assertEqual(report['refunds_cents'],self.order['total_cents'])
        self.assertEqual(report['net_sales_cents'],0)

if __name__=='__main__':unittest.main()

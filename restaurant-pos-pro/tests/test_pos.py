"""Run: python -m unittest discover -s tests -v"""
import os
import tempfile
import unittest
from pathlib import Path
from fastapi.testclient import TestClient

DB_TMP=tempfile.TemporaryDirectory()
os.environ['POS_DATABASE']=str(Path(DB_TMP.name)/'tests.db')
from app import app

class RestaurantPOSFlow(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.client=TestClient(app)
        cls.client.__enter__()

    @classmethod
    def tearDownClass(cls):
        cls.client.__exit__(None,None,None)

    def test_01_setup(self):
        c=self.client
        self.assertFalse(c.get('/api/session').json()['configured'])
        res=c.post('/api/setup',json={'restaurant':'Olive & Stone','name':'Maya Owner','username':'owner','password':'Password123!','currency':'PKR'})
        self.assertEqual(res.status_code,200,res.text)
        self.assertTrue(c.get('/api/session').json()['authenticated'])
        self.assertEqual(c.post('/api/setup',json={'restaurant':'Another','name':'A','username':'x','password':'Password123!'}).status_code,409)
        type(self).csrf=c.get('/api/session').json()['csrf']
        type(self).headers={'X-CSRF-Token':self.csrf}

    def test_02_roles_and_setup_data(self):
        d=self.client.get('/api/bootstrap').json()
        self.assertEqual(len(d['tables']),12)
        self.assertEqual(len(d['menu']),8)
        self.assertNotIn('password_hash',str(d))
        self.assertEqual(self.client.post('/api/menu',json={'name':'X','category':'A','price_cents':100}).status_code,403)
        res=self.client.post('/api/staff',headers=self.headers,json={'name':'Chef','username':'chef','password':'Password123!','role':'kitchen'})
        self.assertEqual(res.status_code,200,res.text)

    def test_03_orders_workflow(self):
        c=self.client
        d=c.get('/api/bootstrap').json()
        item=d['menu'][0];table=d['tables'][0]
        type(self).item=item
        type(self).table=table
        payload={'service_type':'dine_in','table_id':table['id'],'items':[{'item_id':item['id'],'quantity':2}], 'discount_cents':100}
        result=c.post('/api/orders',headers=self.headers,json=payload)
        self.assertEqual(result.status_code,200,result.text)
        o=result.json()['order'];type(self).order=o
        self.assertEqual(o['subtotal_cents'],item['price_cents']*2)
        self.assertEqual(o['total_cents'],item['price_cents']*2-100)
        self.assertEqual(c.post('/api/orders',headers=self.headers,json=payload).status_code,409)
        self.assertEqual(c.post(f"/api/orders/{o['id']}/status",headers=self.headers,json={'status':'preparing'}).status_code,200)
        self.assertEqual(c.post(f"/api/orders/{o['id']}/status",headers=self.headers,json={'status':'ready'}).status_code,200)
        self.assertEqual(c.post(f"/api/orders/{o['id']}/checkout",headers=self.headers,json={'payments':[{'method':'cash','amount_cents':100}]}).status_code,422)
        paid=c.post(f"/api/orders/{o['id']}/checkout",headers=self.headers,json={'payments':[{'method':'cash','amount_cents':o['total_cents']-200},{'method':'card','amount_cents':200}]})
        self.assertEqual(paid.status_code,200,paid.text)
        self.assertEqual(paid.json()['order']['status'],'paid')
        self.assertEqual(c.post(f"/api/orders/{o['id']}/checkout",headers=self.headers,json={'payments':[{'method':'cash','amount_cents':o['total_cents']}]}).status_code,409)
        self.assertEqual(c.get(f"/api/orders/{o['id']}/receipt").json()['order']['payments'][1]['method'],'card')
        self.assertEqual(c.post('/api/orders',headers=self.headers,json=payload).status_code,200)

    def test_04_reports(self):
        from datetime import datetime,timezone
        report=self.client.get('/api/reports?date='+datetime.now(timezone.utc).date().isoformat())
        self.assertEqual(report.status_code,200,report.text)
        data=report.json()
        self.assertGreaterEqual(data['sales_cents'],self.order['total_cents'])
        self.assertGreaterEqual(data['orders'],1)
        self.assertTrue(len(data['payments'])>=2)

    def test_05_kitchen_permissions(self):
        # Separate session, same database.
        c=TestClient(app)
        self.assertEqual(c.post('/api/login',json={'username':'chef','password':'Password123!'}).status_code,200)
        ses=c.get('/api/session').json()
        self.assertEqual(ses['user']['role'],'kitchen')
        hdr={'X-CSRF-Token':ses['csrf']}
        self.assertEqual(c.post('/api/menu',headers=hdr,json={'name':'X','category':'Y','price_cents':100}).status_code,403)
        self.assertEqual(c.post('/api/orders',headers=hdr,json={'service_type':'takeaway','items':[{'item_id':self.item['id'],'quantity':1}]}).status_code,403)
        self.assertEqual(c.get('/api/reports').status_code,403)
        self.assertEqual(c.get('/api/bootstrap').status_code,200)
        self.assertEqual(c.post('/api/logout',headers=hdr).status_code,200)
        self.assertFalse(c.get('/api/session').json()['authenticated'])

    def test_06_stock(self):
        c=self.client
        r=c.post('/api/menu',headers=self.headers,json={'name':'Limited Special','category':'Specials','price_cents':15000,'stock':2,'track_stock':True,'active':True})
        self.assertEqual(r.status_code,200,r.text)
        special=next(x for x in c.get('/api/bootstrap').json()['menu'] if x['name']=='Limited Special')
        payload={'service_type':'takeaway','items':[{'item_id':special['id'],'quantity':2}]}
        o=c.post('/api/orders',headers=self.headers,json=payload).json()['order']
        self.assertEqual(c.post(f"/api/orders/{o['id']}/checkout",headers=self.headers,json={'payments':[{'method':'cash','amount_cents':o['total_cents']}]}).status_code,200)
        self.assertEqual(next(x for x in c.get('/api/bootstrap').json()['menu'] if x['id']==special['id'])['stock'],0)
        self.assertEqual(c.post('/api/orders',headers=self.headers,json=payload).status_code,422)


    def test_07_append_and_paid_kitchen(self):
        c=self.client
        payload={'service_type':'takeaway','items':[{'item_id':self.item['id'],'quantity':1}]}
        created=c.post('/api/orders',headers=self.headers,json=payload)
        self.assertEqual(created.status_code,200,created.text)
        order=created.json()['order']
        more=c.post(f"/api/orders/{order['id']}/items",headers=self.headers,
                    json={'items':[{'item_id':self.item['id'],'quantity':1}],'note':'Extra sauce'})
        self.assertEqual(more.status_code,200,more.text)
        updated=more.json()['order']
        self.assertEqual(len(updated['items']),2)
        self.assertEqual(updated['subtotal_cents'],self.item['price_cents']*2)
        paid=c.post(f"/api/orders/{order['id']}/checkout",headers=self.headers,
                    json={'payments':[{'method':'cash','amount_cents':updated['total_cents']}]})
        self.assertEqual(paid.status_code,200,paid.text)
        self.assertEqual(paid.json()['order']['kitchen_status'],'new')
        status=c.post(f"/api/orders/{order['id']}/status",headers=self.headers,json={'status':'preparing'})
        self.assertEqual(status.status_code,200,status.text)
        from_db=c.get(f"/api/orders/{order['id']}/receipt").json()['order']
        self.assertEqual(from_db['status'],'paid')
        self.assertEqual(from_db['kitchen_status'],'preparing')
        self.assertEqual(c.post(f"/api/orders/{order['id']}/items",headers=self.headers,json={'items':[{'item_id':self.item['id'],'quantity':1}]}).status_code,409)

if __name__=='__main__': unittest.main()

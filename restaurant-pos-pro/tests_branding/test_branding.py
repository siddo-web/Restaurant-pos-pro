"""Security, persistence, role and branch-isolation tests for restaurant branding."""
import base64
import io
import os
import tempfile
import unittest
from pathlib import Path
from PIL import Image
from fastapi.testclient import TestClient

TEMP = tempfile.TemporaryDirectory()
os.environ['POS_DATABASE'] = str(Path(TEMP.name) / 'branding_test.db')
from app import app
from branding import ASSET_DIR


class BrandingFlow(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.c = TestClient(app)
        cls.c.__enter__()
        response = cls.c.post('/api/setup', json={'restaurant': 'Cedar & Thyme', 'name': 'Owner',
                  'username': 'brand-owner', 'password': 'ValidPassword123', 'currency': 'PKR'})
        assert response.status_code == 200, response.text
        cls.h = {'x-csrf-token': cls.c.get('/api/session').json()['csrf']}

    @classmethod
    def tearDownClass(cls):
        cls.c.__exit__(None, None, None)

    @staticmethod
    def logo_image():
        img = Image.new('RGB', (200, 80), (30, 115, 85))
        out = io.BytesIO()
        img.save(out, format='JPEG')
        return base64.b64encode(out.getvalue()).decode('ascii')

    def test_01_defaults_and_theme_persistence(self):
        default = self.c.get('/api/branding')
        self.assertEqual(default.status_code, 200)
        self.assertEqual(default.json()['theme'], 'classic')
        self.assertIsNone(default.json()['logo_url'])
        self.assertEqual(self.c.post('/api/branding', json={'theme': 'midnight'}, headers={}).status_code, 403)
        bad = self.c.post('/api/branding', json={'theme': 'unknown'}, headers=self.h)
        self.assertEqual(bad.status_code, 422)
        save = self.c.post('/api/branding', headers=self.h, json={
            'theme': 'midnight', 'display_name': 'The Garden Kitchen', 'tagline': 'Local cuisine.'})
        self.assertEqual(save.status_code, 200, save.text)
        value = self.c.get('/api/branding').json()
        self.assertEqual(value['theme'], 'midnight')
        self.assertEqual(value['display_name'], 'The Garden Kitchen')
        token=self.c.get('/api/pro/tables/qr').json()['links'][0]['url'].split('?t=')[1]
        public=self.c.get('/api/public/catalog?t='+token)
        self.assertEqual(public.json()['branding']['theme'], 'midnight')
        self.assertEqual(public.json()['branding']['display_name'], 'The Garden Kitchen')

    def test_02_upload_replace_remove_and_validation(self):
        invalid = self.c.post('/api/branding/logo', json={
            'image_base64': base64.b64encode(b'<svg><script>alert(1)</script></svg>').decode('ascii'),
            'mime_type': 'image/png'}, headers=self.h)
        self.assertEqual(invalid.status_code, 422)
        self.assertEqual(self.c.post('/api/branding/logo', json={
            'image_base64': self.logo_image(), 'mime_type': 'image/png'}, headers=self.h).status_code, 422)
        response=self.c.post('/api/branding/logo', json={
            'image_base64': self.logo_image(), 'mime_type': 'image/jpeg'}, headers=self.h)
        self.assertEqual(response.status_code, 200, response.text)
        url=response.json()['branding']['logo_url']
        self.assertIn('/api/branding/logo/1?', url)
        image=self.c.get(url)
        self.assertEqual(image.status_code, 200)
        self.assertEqual(image.headers['content-type'], 'image/png')
        self.assertEqual(image.headers['x-content-type-options'], 'nosniff')
        self.assertTrue(image.content.startswith(b'\x89PNG\r\n\x1a\n'))
        self.assertTrue((ASSET_DIR/'logo-1.png').exists())
        remove=self.c.delete('/api/branding/logo', headers=self.h)
        self.assertEqual(remove.status_code, 200)
        self.assertIsNone(remove.json()['branding']['logo_url'])
        self.assertEqual(self.c.get(url).status_code,404)
        self.assertFalse((ASSET_DIR/'logo-1.png').exists())
        # Reupload to check branch independence below.
        self.assertEqual(self.c.post('/api/branding/logo', headers=self.h, json={
            'image_base64':self.logo_image(),'mime_type':'image/jpeg'}).status_code,200)

    def test_03_staff_permissions_and_branch_isolation(self):
        self.assertEqual(self.c.post('/api/staff', json={'name':'Staff', 'username':'brand-cashier',
            'password':'ValidPassword123', 'role':'cashier'}, headers=self.h).status_code,200)
        cashier=TestClient(app)
        try:
            self.assertEqual(cashier.post('/api/login',json={
                'username':'brand-cashier','password':'ValidPassword123'}).status_code,200)
            h={'x-csrf-token':cashier.get('/api/session').json()['csrf']}
            self.assertEqual(cashier.get('/api/branding').status_code,200)
            self.assertEqual(cashier.post('/api/branding',json={'theme':'ocean'},headers=h).status_code,403)
            self.assertEqual(cashier.delete('/api/branding/logo',headers=h).status_code,403)
            self.assertEqual(cashier.post('/api/branding/logo', headers=h,json={
                'image_base64': self.logo_image(),'mime_type':'image/jpeg'}).status_code,403)
        finally:cashier.close()
        branch=self.c.post('/api/pro/branches',json={
            'name':'West End','code':'WEST','clone_menu':False}, headers=self.h)
        self.assertEqual(branch.status_code,200,branch.text)
        other=branch.json()['branch_id']
        self.assertEqual(self.c.post('/api/pro/branches/switch',json={'branch_id':other},headers=self.h).status_code,200)
        self.assertEqual(self.c.get('/api/branding').json()['theme'],'classic')
        self.assertIsNone(self.c.get('/api/branding').json()['logo_url'])
        self.assertEqual(self.c.post('/api/branding',headers=self.h,json={'theme':'forest',
                           'display_name':'West End Kitchen','tagline':''}).status_code,200)
        self.assertEqual(self.c.post('/api/pro/branches/switch',json={'branch_id':1},headers=self.h).status_code,200)
        self.assertEqual(self.c.get('/api/branding').json()['theme'],'midnight')
        self.assertIsNotNone(self.c.get('/api/branding').json()['logo_url'])
        actions=[a['action'] for a in self.c.get('/api/pro/bootstrap').json()['recent_audits']]
        self.assertIn('branding_update',actions)
        self.assertIn('logo_upload',actions)


if __name__=='__main__':unittest.main()

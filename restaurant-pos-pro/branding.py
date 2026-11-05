"""Branch-scoped visual branding, safe restaurant logo uploads and CSS theme selection.

Branding assets stay alongside the database, never inside the public static directory.
The API accepts base64 image data rather than multipart so the existing FastAPI
installation does not require python-multipart.
"""
import base64
import binascii
import io
import os
import tempfile
from pathlib import Path
from typing import Literal

from fastapi import HTTPException, Request
from fastapi.responses import FileResponse
from PIL import Image, UnidentifiedImageError
from pydantic import BaseModel, Field

from app import DB_PATH, auth, database, require_text
from enhancements import log

THEMES = {'classic', 'midnight', 'ivory', 'ocean', 'forest', 'saffron'}
ASSET_DIR = DB_PATH.parent / 'branding'
MAX_FILE_SIZE = 2 * 1024 * 1024
MAX_PIXELS = 12_000_000
Image.MAX_IMAGE_PIXELS = MAX_PIXELS


def upgrade():
    with database() as db:
        db.execute('''CREATE TABLE IF NOT EXISTS branch_branding (
            branch_id INTEGER PRIMARY KEY REFERENCES branches(id),
            theme TEXT NOT NULL DEFAULT 'classic',
            display_name TEXT NOT NULL DEFAULT '',
            tagline TEXT NOT NULL DEFAULT '',
            logo_present INTEGER NOT NULL DEFAULT 0,
            logo_version INTEGER NOT NULL DEFAULT 0,
            updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        )''')
        # No existing orders/menu/payments/settings are changed.
        db.execute('''INSERT OR IGNORE INTO branch_branding(branch_id)
                      SELECT id FROM branches''')


def _record(db, branch_id):
    row = db.execute('SELECT * FROM branch_branding WHERE branch_id=?', (branch_id,)).fetchone()
    return dict(row) if row else {'branch_id': branch_id, 'theme': 'classic',
                                   'display_name': '', 'tagline': '', 'logo_present': 0,
                                   'logo_version': 0}


def public_branding(db, branch_id):
    brand = _record(db, branch_id)
    return {'branch_id': branch_id,
            'theme': brand['theme'] if brand['theme'] in THEMES else 'classic',
            'display_name': brand['display_name'], 'tagline': brand['tagline'],
            'logo_url': f'/api/branding/logo/{branch_id}?v={brand["logo_version"]}' if brand['logo_present'] else None}


class BrandingSettings(BaseModel):
    theme: Literal['classic', 'midnight', 'ivory', 'ocean', 'forest', 'saffron']
    display_name: str = Field(default='', max_length=120)
    tagline: str = Field(default='', max_length=100)


class LogoUpload(BaseModel):
    image_base64: str = Field(min_length=16, max_length=3_000_000)
    mime_type: Literal['image/png', 'image/jpeg', 'image/webp']


def decode_logo(payload):
    try:
        raw = base64.b64decode(payload.image_base64, validate=True)
    except (binascii.Error, ValueError):
        raise HTTPException(422, 'Invalid image data')
    if len(raw) > MAX_FILE_SIZE:
        raise HTTPException(413, 'Logo must be 2 MB or smaller')
    try:
        with Image.open(io.BytesIO(raw)) as source:
            if source.format not in ('PNG', 'JPEG', 'WEBP'):
                raise HTTPException(422, 'Please upload a PNG, JPG or WebP image')
            if (source.format == 'PNG' and payload.mime_type != 'image/png' or
                source.format == 'JPEG' and payload.mime_type != 'image/jpeg' or
                source.format == 'WEBP' and payload.mime_type != 'image/webp'):
                raise HTTPException(422, 'Image format does not match selected file')
            width, height = source.size
            if width <= 0 or height <= 0 or width * height > MAX_PIXELS:
                raise HTTPException(422, 'Image resolution is too large')
            source.load()
            im = source.convert('RGBA')
            im.thumbnail((640, 640), Image.Resampling.LANCZOS)
            # Image data is re-encoded; uploaded metadata/scripts are discarded.
            buffer = io.BytesIO()
            im.save(buffer, 'PNG', optimize=True)
            return buffer.getvalue()
    except (UnidentifiedImageError, OSError, ValueError, Image.DecompressionBombError):
        raise HTTPException(422, 'Unreadable or unsafe image')


def install(app):
    upgrade()

    @app.get('/api/branding')
    def get_branding(request: Request):
        with database() as db:
            user = auth(request, db)
            return public_branding(db, user['branch_id'])

    @app.post('/api/branding')
    def save_branding(payload: BrandingSettings, request: Request):
        with database() as db:
            user = auth(request, db, ['admin'], True)
            name = payload.display_name.strip()
            tagline = payload.tagline.strip()
            if len(name) > 120 or len(tagline) > 100:
                raise HTTPException(422, 'Branding text is too long')
            db.execute('''INSERT INTO branch_branding(branch_id,theme,display_name,tagline,updated_at)
                        VALUES(?,?,?,?,CURRENT_TIMESTAMP)
                        ON CONFLICT(branch_id) DO UPDATE SET theme=excluded.theme,
                        display_name=excluded.display_name,tagline=excluded.tagline,
                        updated_at=CURRENT_TIMESTAMP''',
                        (user['branch_id'], payload.theme, name, tagline))
            log(db, user, 'branding_update', 'branch', user['branch_id'], payload.theme)
            return {'ok': True, 'branding': public_branding(db, user['branch_id'])}

    @app.post('/api/branding/logo')
    def upload_brand_logo(payload: LogoUpload, request: Request):
        with database() as db:
            user = auth(request, db, ['admin'], True)
            content = decode_logo(payload)
            branch_id = user['branch_id']
            ASSET_DIR.mkdir(parents=True, exist_ok=True)
            filepath = ASSET_DIR / f'logo-{branch_id}.png'
            tmp_path = None
            try:
                with tempfile.NamedTemporaryFile(mode='wb', dir=str(ASSET_DIR), prefix='.upload-', suffix='.png', delete=False) as tmp:
                    tmp.write(content)
                    tmp_path = tmp.name
                os.replace(tmp_path, filepath)
                db.execute('''INSERT INTO branch_branding(branch_id,logo_present,logo_version,updated_at)
                              VALUES(?,1,1,CURRENT_TIMESTAMP)
                              ON CONFLICT(branch_id) DO UPDATE SET logo_present=1,
                                logo_version=logo_version+1,updated_at=CURRENT_TIMESTAMP''', (branch_id,))
                log(db, user, 'logo_upload', 'branch', branch_id)
            finally:
                if tmp_path and os.path.exists(tmp_path):
                    os.unlink(tmp_path)
            return {'ok': True, 'branding': public_branding(db, branch_id)}

    @app.delete('/api/branding/logo')
    def remove_brand_logo(request: Request):
        with database() as db:
            user = auth(request, db, ['admin'], True)
            bid = user['branch_id']
            db.execute('''UPDATE branch_branding SET logo_present=0,
                          logo_version=logo_version+1,updated_at=CURRENT_TIMESTAMP WHERE branch_id=?''', (bid,))
            log(db, user, 'logo_remove', 'branch', bid)
            (ASSET_DIR / f'logo-{bid}.png').unlink(missing_ok=True)
            return {'ok': True, 'branding': public_branding(db, bid)}

    @app.get('/api/branding/logo/{branch_id}')
    def brand_logo(branch_id: int):
        # Logos are public assets (also shown on QR menus); no other branch data leaks.
        with database() as db:
            row = db.execute('''SELECT logo_present FROM branch_branding b
                 JOIN branches r ON r.id=b.branch_id WHERE b.branch_id=? AND r.active=1''', (branch_id,)).fetchone()
            if not row or not row['logo_present']:
                raise HTTPException(404, 'Logo not found')
        path = ASSET_DIR / f'logo-{branch_id}.png'
        if not path.is_file():
            raise HTTPException(404, 'Logo not found')
        return FileResponse(path, media_type='image/png', headers={
            'X-Content-Type-Options': 'nosniff',
            'Cache-Control': 'private, max-age=0, must-revalidate'
        })

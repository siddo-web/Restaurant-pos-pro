"""Safe backup of a live SQLite POS database using sqlite3's online backup API.
Run from the repository root: python tools/backup_db.py
"""
import os
import shutil
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
source = Path(os.getenv('POS_DATABASE',str(ROOT/'data'/'restaurant.db'))).resolve()
if not source.is_file():
    raise SystemExit(f'Cannot find database: {source}')
backups=ROOT/'backups'
backups.mkdir(exist_ok=True)
stamp=f'{datetime.now(timezone.utc):%Y%m%dT%H%M%SZ}'
target=backups/f'pos-{stamp}.db'
with sqlite3.connect(source) as src:
    with sqlite3.connect(target) as dst:
        src.backup(dst)
        status=dst.execute('PRAGMA integrity_check').fetchone()[0]
        if status != 'ok':
            target.unlink(missing_ok=True)
            raise SystemExit(f'Backup integrity check failed: {status}')
print(f'Backup created: {target}')

# Restaurant logos are filesystem assets, not database blobs. Preserve them too.
source_assets=source.parent/'branding'
if source_assets.is_dir():
    asset_backup=backups/f'branding-{stamp}'
    shutil.copytree(source_assets,asset_backup)
    print(f'Logo assets backed up: {asset_backup}')

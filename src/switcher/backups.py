import datetime as dt
import gzip
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import uuid

OWNER = 'codex-provider-switcher/v1'
DIGEST = re.compile(r'^[a-f0-9]{64}$')
IDENT = re.compile(r'^[0-9TZ-]+-[a-f0-9]{12}$')


def sync_directory(path):
    fd=os.open(path,os.O_RDONLY | getattr(os,'O_DIRECTORY',0))
    try:os.fsync(fd)
    finally:os.close(fd)


def atomic_write(path, content):
    path = Path(path)
    if path.is_symlink():
        raise ValueError('拒绝写入符号链接：' + path.name)
    tmp = path.with_name('.' + path.name + '.' + uuid.uuid4().hex)
    try:
        with open(tmp, 'xb') as f:
            os.chmod(tmp, 0o600)
            f.write(content)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
        sync_directory(path.parent)
    finally:
        tmp.unlink(missing_ok=True)


class BackupStore:
    def __init__(self, root):
        root = Path(root)
        if root.is_symlink() or root.parent.is_symlink():
            raise ValueError('备份目录不能是符号链接')
        self.root = root
        self.root.mkdir(parents=True, mode=0o700, exist_ok=True)
        sync_directory(self.root.parent)
        os.chmod(self.root, 0o700)
        marker = self.root / '.owner'
        if marker.exists() and marker.read_text() != OWNER:
            raise ValueError('该目录不属于本工具')
        if not marker.exists():
            if any(self.root.iterdir()):
                raise ValueError('不能接管已有的备份目录')
            atomic_write(marker, OWNER.encode())
        self.blobs = self.root / 'blobs'
        if self.blobs.is_symlink():
            raise ValueError('备份内容目录不能是符号链接')
        self.blobs.mkdir(mode=0o700, exist_ok=True)
        sync_directory(self.root)

    def blob_path(self, digest):
        if not DIGEST.fullmatch(digest):
            raise ValueError('备份散列格式无效')
        path = self.blobs / (digest + '.gz')
        if path.is_symlink():
            raise ValueError('备份内容不能是符号链接')
        return path

    def put(self, value):
        temp = self.blobs / ('.pending-' + uuid.uuid4().hex)
        h = hashlib.sha256()
        try:
            with open(temp, 'xb') as raw:
                os.chmod(temp, 0o600)
                with gzip.GzipFile(fileobj=raw, mode='wb', compresslevel=1, mtime=0) as out:
                    if isinstance(value, bytes):
                        h.update(value); out.write(value)
                    else:
                        path = Path(value)
                        if path.is_symlink() or not path.is_file():
                            raise ValueError('备份源不是普通文件：' + str(path))
                        with path.open('rb') as source:
                            while chunk := source.read(1024 * 1024):
                                h.update(chunk); out.write(chunk)
                raw.flush(); os.fsync(raw.fileno())
            target = self.blob_path(h.hexdigest())
            # Replace even an existing blob: a corrupt prior copy cannot taint a new snapshot.
            os.replace(temp, target)
            sync_directory(self.blobs)
            return h.hexdigest()
        finally:
            temp.unlink(missing_ok=True)

    def create(self, files, routes, created_at=None):
        now = dt.datetime.now(dt.timezone.utc)
        ident = now.strftime('%Y%m%dT%H%M%SZ') + '-' + uuid.uuid4().hex[:12]
        folder = self.root / ident
        folder.mkdir(mode=0o700)
        sync_directory(self.root)
        manifest = {'owner': OWNER, 'version': 1, 'id': ident,
                    'created_at': created_at or now.isoformat(), 'complete': False,
                    'files': {}, 'routes': routes}
        for name, value in files.items():
            p = Path(name)
            if p.is_absolute() or '..' in p.parts:
                raise ValueError('备份相对路径无效')
            manifest['files'][name] = self.put(value)
        manifest['complete'] = True
        atomic_write(folder/'manifest.json', json.dumps(manifest, ensure_ascii=False).encode())
        return manifest

    def read(self, ident):
        if not isinstance(ident, str) or not IDENT.fullmatch(ident):
            raise ValueError('备份 ID 无效')
        folder = self.root / ident
        path = folder/'manifest.json'
        if folder.is_symlink() or path.is_symlink() or not path.is_file():
            raise ValueError('备份不是有效普通目录')
        manifest = json.loads(path.read_text())
        if (manifest.get('owner') != OWNER or manifest.get('version') != 1 or
                manifest.get('id') != ident or manifest.get('complete') is not True):
            raise ValueError('备份清单无效')
        stamp = dt.datetime.fromisoformat(manifest['created_at'])
        if stamp.tzinfo is None:
            raise ValueError('备份时间缺少时区')
        for name, digest in manifest['files'].items():
            if Path(name).is_absolute() or '..' in Path(name).parts:
                raise ValueError('备份路径越界')
            self.blob_path(digest)
        return manifest

    def list(self):
        result = []
        for folder in self.root.iterdir():
            if folder.is_symlink() or not folder.is_dir() or not IDENT.fullmatch(folder.name):
                continue
            try:
                result.append(self.read(folder.name))
            except (ValueError, OSError, KeyError, TypeError):
                continue
        return sorted(result, key=lambda s: (s['created_at'], s['id']), reverse=True)

    def content(self, ident, name):
        manifest = self.read(ident)
        digest = manifest['files'][name]
        try:
            with gzip.open(self.blob_path(digest), 'rb') as f:
                data = f.read()
        except (OSError, EOFError) as exc:
            raise ValueError('备份内容损坏') from exc
        if hashlib.sha256(data).hexdigest() != digest:
            raise ValueError('备份校验失败')
        return data

    def verify(self, ident):
        manifest = self.read(ident)
        for digest in set(manifest['files'].values()):
            h = hashlib.sha256()
            try:
                with gzip.open(self.blob_path(digest), 'rb') as f:
                    while chunk := f.read(1024 * 1024):h.update(chunk)
            except (OSError, EOFError) as exc:
                raise ValueError('备份内容损坏') from exc
            if h.hexdigest() != digest:
                raise ValueError('备份校验失败')
        return manifest

    def prune(self, keep=10, days=30, min_keep=3, protected=()):
        if not (3 <= keep <= 100 and 1 <= days <= 3650 and min_keep >= 3):
            raise ValueError('至少保留 3 份；数量范围 3–100，天数范围 1–3650')
        snapshots = self.list()
        cutoff = dt.datetime.now(dt.timezone.utc) - dt.timedelta(days=days)
        removed = []
        protected = set(protected)
        for index, manifest in enumerate(snapshots):
            old = dt.datetime.fromisoformat(manifest['created_at']) < cutoff
            if index < min_keep or manifest['id'] in protected or not (index >= keep or old):
                continue
            # Only our single manifest is removed; unknown extra files prevent deletion.
            folder = self.root / manifest['id']
            if {p.name for p in folder.iterdir()} != {'manifest.json'}:
                continue
            self.read(manifest['id'])
            (folder/'manifest.json').unlink()
            folder.rmdir()
            removed.append(manifest['id'])
        if removed:sync_directory(self.root)
        # Invalid/incomplete manifests may reference blobs: refuse garbage collection then.
        if any(p.is_dir() and not p.is_symlink() and IDENT.fullmatch(p.name) and
               p.name not in {m['id'] for m in self.list()} for p in self.root.iterdir()):
            return removed
        used = {d for s in self.list() for d in s['files'].values()}
        for blob in self.blobs.iterdir():
            if blob.is_symlink() or not blob.is_file() or not blob.name.endswith('.gz'):
                continue
            digest = blob.name[:-3]
            if DIGEST.fullmatch(digest) and digest not in used:
                blob.unlink()
        sync_directory(self.blobs)
        return removed

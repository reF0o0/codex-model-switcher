"""Repair only verified DeepSeek replay shapes; preserve every byte offset."""
from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path
import shutil
import tempfile

from .backups import sync_directory


@dataclass(frozen=True)
class Change:
    offset: int
    before: bytes
    after: bytes

    def reverse(self):
        return Change(self.offset, self.after, self.before)


def records(path):
    with path.open('rb') as stream:
        offset = 0
        for ordinal, raw in enumerate(stream, 1):
            try:
                record = json.loads(raw)
                if not isinstance(record, dict):
                    raise ValueError()
            except (ValueError, UnicodeError):
                raise ValueError(f'无法自动修复：{path.name} 第 {ordinal} 行格式无效；未写入任何会话') from None
            yield offset, ordinal, raw, record
            offset += len(raw)


def plan_repairs(path):
    """Use the exact matching completed display event, never invent reasoning."""
    completed = {}
    for _, ordinal, _, row in records(path):
        payload = row.get('payload', {})
        if not isinstance(payload, dict):
            continue
        item = payload.get('item', {})
        if (row.get('type') == 'event_msg' and payload.get('type') == 'item_completed'
                and isinstance(item, dict) and item.get('type') == 'Reasoning'
                and isinstance(item.get('id'), str)):
            ident = item['id']
            if ident in completed and completed[ident]['payload']['item'] != item:
                raise ValueError(f'无法自动修复：{path.name} 第 {ordinal} 行历史备注重复且不一致')
            completed[ident] = row

    changes = []
    model = None
    for offset, ordinal, raw, row in records(path):
        payload = row.get('payload', {})
        if not isinstance(payload, dict):
            continue
        if row.get('type') == 'turn_context':
            model = payload.get('model')
        # Compacted histories need their own validation strategy. Refuse to
        # partially repair them, including when older replay items still exist.
        if row.get('type') == 'compacted':
            if any(isinstance(x, dict) and x.get('type') == 'reasoning' and x.get('content')
                   for x in payload.get('replacement_history', []) or []):
                raise ValueError(f'无法自动修复：{path.name} 的压缩历史包含未知推理格式，请使用兼容接续')
        if (row.get('type') != 'response_item' or payload.get('type') != 'reasoning'
                or not isinstance(model, str) or not model.lower().startswith('deepseek')):
            continue
        content = payload.get('content')
        display = completed.get(payload.get('id'))
        if (not isinstance(content, list) or not content
                or any(not isinstance(x, dict) or x.get('type') != 'reasoning_text'
                       or not isinstance(x.get('text'), str) for x in content)
                or display is None
                or display['payload']['item'].get('raw_content') != [x['text'] for x in content]):
            raise ValueError(f'无法自动修复：{path.name} 第 {ordinal} 行缺少匹配的推理历史备注，请使用兼容接续')
        display = dict(display, timestamp=row.get('timestamp'))
        display['payload'] = dict(display['payload'])
        display['payload'].pop('started_at_ms', None)
        display['payload'].pop('completed_at_ms', None)
        encoded = json.dumps(display, ensure_ascii=False, separators=(',', ':')).encode()
        padding = len(raw) - 1 - len(encoded)
        if padding < 0 or not raw.endswith(b'\n'):
            raise ValueError(f'无法自动修复：{path.name} 第 {ordinal} 行没有足够的等长替换空间')
        changes.append(Change(offset, raw, encoded + b' ' * padding + b'\n'))
    return changes


def replace_ranges(path, changes):
    """Validate and atomically replace equal-length ranges, keeping later data."""
    if path.is_symlink() or not path.is_file():
        raise ValueError('修复目标不是普通会话文件')
    changes = sorted(changes, key=lambda x: x.offset)
    end = 0
    for change in changes:
        if change.offset < end or len(change.before) != len(change.after):
            raise ValueError('修复范围重叠或字节长度发生变化')
        end = change.offset + len(change.before)
    temp = None
    try:
        with path.open('rb') as source:
            original_stat = os.fstat(source.fileno())
            with tempfile.NamedTemporaryFile(prefix='.compatibility-', dir=path.parent, delete=False) as target:
                temp = Path(target.name)
                os.chmod(temp, original_stat.st_mode & 0o777)
                shutil.copyfileobj(source, target, 1024 * 1024)
                for change in changes:
                    source.seek(change.offset)
                    if source.read(len(change.before)) != change.before:
                        raise ValueError('会话在检查后发生变化，已停止修复')
                    target.seek(change.offset)
                    target.write(change.after)
                target.flush()
                os.fsync(target.fileno())
            current = path.stat()
            if (current.st_ino, current.st_size, current.st_mtime_ns) != (
                    original_stat.st_ino, original_stat.st_size, original_stat.st_mtime_ns):
                raise ValueError('会话在修复期间发生变化，已停止写入')
        os.replace(temp, path)
        sync_directory(path.parent)
    finally:
        if temp is not None:
            temp.unlink(missing_ok=True)


def describe(changes):
    return [{'offset': c.offset, 'length': len(c.before),
             'repaired_sha256': hashlib.sha256(c.after).hexdigest()} for c in changes]


def restore_changes(path, original, spans):
    changes = []
    with path.open('rb') as stream:
        for span in spans:
            offset, length = span['offset'], span['length']
            if not isinstance(offset, int) or not isinstance(length, int) or offset < 0 or length < 1:
                raise ValueError('修复备份范围无效')
            before = original[offset:offset + length]
            stream.seek(offset)
            current = stream.read(length)
            if len(before) != length:
                raise ValueError('修复备份长度无效')
            if current == before:
                continue
            if hashlib.sha256(current).hexdigest() != span['repaired_sha256']:
                raise ValueError('修复位置已被其他操作修改，已停止恢复；新消息保留')
            changes.append(Change(offset, current, before))
    return changes

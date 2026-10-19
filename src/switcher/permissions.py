"""Check access using the same Python child process that performs app operations.

No settings, histories, databases, or backups are edited. Directory probes create,
rename and remove a private temporary file, testing the operations used by atomic
writes. This reports effective access, not macOS's private TCC database state.
"""
import errno
import os
from pathlib import Path
import stat
import tempfile
import time


class PermissionCheckError(ValueError):
    def __init__(self, report):
        self.report = report
        failed = next(c for c in report['checks'] if c['status'] not in ('ok', 'optional'))
        super().__init__('文件访问检查未通过：' + failed['path'] + '。' + failed['detail'] +
                         ' 请在“文件访问权限”中申请目录访问，或打开系统权限设置后重新检查。')


def _probe_directory(path):
    # Do not infer write access from mode bits: ACLs and macOS privacy rules apply.
    descriptor, name = tempfile.mkstemp(prefix='.switcher-access-', dir=path)
    source = Path(name)
    target = source.with_name(source.name + '.renamed')
    try:
        with os.fdopen(descriptor, 'wb') as stream:
            stream.write(b'permission check\n')
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(source, target)
        target.unlink()
    finally:
        source.unlink(missing_ok=True)
        target.unlink(missing_ok=True)


def _probe_file(path, writable):
    # Opening without truncation tests access; no bytes are changed.
    with path.open('r+b' if writable else 'rb') as stream:
        stream.read(1)


def _check_path(path, directory=False, recursive=False, writable=True):
    if path.is_symlink():
        raise OSError(errno.ELOOP, 'symbolic link', str(path))
    info = path.stat()
    expected = stat.S_ISDIR(info.st_mode) if directory else stat.S_ISREG(info.st_mode)
    if not expected:
        raise OSError(errno.EINVAL, 'unexpected file type', str(path))
    if not directory:
        _probe_file(path, writable)
        return 1
    _probe_directory(path)
    # Scandir is necessary: directory creation alone does not prove enumeration access.
    with os.scandir(path) as entries:
        list(entries)
    if not recursive:
        return 0
    count = 0
    def walk_error(error):
        raise error
    for root, directories, files in os.walk(path, onerror=walk_error, followlinks=False):
        folder = Path(root)
        if folder != path:
            _probe_directory(folder)
        for name in directories:
            child = folder / name
            if child.is_symlink():
                raise OSError(errno.ELOOP, 'symbolic link', str(child))
        for name in files:
            child = folder / name
            if writable and child.suffix != '.jsonl':
                continue
            if child.is_symlink():
                raise OSError(errno.ELOOP, 'symbolic link', str(child))
            if not child.is_file():
                raise OSError(errno.EINVAL, 'unexpected file type', str(child))
            _probe_file(child, writable)
            count += 1
    return count


def check_permissions(home=None, allow_missing_config=False):
    home = Path(home or Path.home() / '.codex').expanduser().absolute()
    checks = []

    def check(label, path, required=False, **options):
        row = {'label': label, 'path': str(path), 'status': 'ok', 'detail': '可访问', 'files_checked': 0}
        try:
            row['files_checked'] = _check_path(path, **options)
        except FileNotFoundError as error:
            # Only the optional root may be absent; a broken nested entry is an error.
            absent_root = error.filename == str(path)
            row.update(status='missing' if required or not absent_root else 'optional',
                       detail='路径不存在，请确认 Codex 已在本机运行并创建配置。' if required or not absent_root
                       else '尚未创建；需要时由工具建立。')
        except OSError as error:
            if error.errno in (errno.EACCES, errno.EPERM):
                detail = '系统拒绝访问，请授予文件读写权限。'
            elif error.errno == errno.ELOOP:
                detail = '路径含有符号链接；工具不会自动修改其指向的文件。'
            elif error.errno == errno.EROFS:
                detail = '文件系统为只读，无法安全写入。'
            elif error.errno == errno.ENOSPC:
                detail = '磁盘空间不足，无法完成写入检查。'
            else:
                detail = '无法完成文件读写检查（' + type(error).__name__ + '）。'
            row.update(status='blocked', path=str(error.filename or path), detail=detail)
        checks.append(row)
        return row['status'] == 'ok'

    if check('Codex 数据目录', home, required=True, directory=True):
        check('配置文件', home / 'config.toml', required=not allow_missing_config)
        check('会话数据库', home / 'state_5.sqlite', required=True)
        for name, label in [('thread_history_1.sqlite', '分页历史数据库'), ('models.json', '模型目录')]:
            check(label, home / name)
        for database in ('state_5.sqlite', 'thread_history_1.sqlite'):
            for suffix in ('-wal', '-shm', '-journal'):
                check('数据库辅助文件：' + database + suffix, home / (database + suffix))
        for name, label in [('sessions', '会话文件'), ('archived_sessions', '归档会话')]:
            check(label, home / name, directory=True, recursive=True)
        # Backups are immutable; reading them and probing their directories is enough.
        check('备份与工具数据', home / 'provider-switcher', directory=True, recursive=True, writable=False)
        for name in ('settings.json', 'search-state.json', 'links.json', 'bridge-state.json', 'last-switch.json', 'pending.json', 'operation.lock'):
            check('工具设置：' + name, home / 'provider-switcher' / name)
    ready = all(c['status'] in ('ok', 'optional') for c in checks)
    return {'ready': ready, 'checked_at': time.time(), 'checks': checks,
            'summary': '文件读写检查通过' if ready else '文件访问检查未通过，写入已暂停'}


def require_permissions(home=None, allow_missing_config=False):
    report = check_permissions(home, allow_missing_config=allow_missing_config)
    if not report['ready']:
        raise PermissionCheckError(report)
    return report

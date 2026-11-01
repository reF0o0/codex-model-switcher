"""Read one conversation's local evidence; never create a Switcher or call a model."""
from contextlib import closing
import json
from pathlib import Path
import sqlite3
import time

from .compatibility import plan_repairs


ADVICE = {
    'history_format': ('历史格式不兼容', '先尝试修复格式；无法安全修复时使用兼容接续，保留原会话。'),
    'quota': ('额度或余额不足', '检查当前服务商的额度或余额，恢复后重试已有接续。'),
    'rate_limit': ('请求频率受限', '稍后重试已有会话，避免连续重复发送。'),
    'network': ('网络或后端连接异常', '检查网络、代理与服务状态，恢复后重试已有会话。'),
    'permission': ('文件访问被拒绝', '使用文件访问权限检查，授权后重新检查。'),
    'authentication': ('登录或密钥无效', '检查当前服务商的登录状态或 API 密钥。'),
    'unknown': ('失败原因尚不明确', '查看原会话的最近错误；不要仅凭本地格式检查判断已修复。'),
    'no_recent_failure': ('最近一轮没有失败记录', '本地记录不能验证当前模型是否可回复；可以打开原会话继续。'),
    'unavailable': ('缺少最近运行记录', '尚不能判断运行失败原因；可以打开原会话查看提示。'),
}


def classify_error(error):
    # Only return fixed labels/advice, never arbitrary server text (which may contain secrets).
    text = (error if isinstance(error, str) else json.dumps(error, ensure_ascii=False)).lower()
    if any(s in text for s in ('array too long', 'array_above_max_length', 'reasoning_text',
                               'missing field call_id', 'missing field `call_id`', 'no tool output found',
                               'expected an array with maximum length 0', 'failed to buffer the request body',
                               'context_length_exceeded', 'maximum context length', 'invalid encrypted content')):
        return 'history_format'
    if any(s in text for s in ('insufficient_quota', 'insufficient balance', 'quota exceeded',
                               'usage limit', 'usage_limit', 'billing', '余额不足', '额度不足')):return 'quota'
    if any(s in text for s in ('rate_limit', 'rate limit', 'too many requests', '429')):return 'rate_limit'
    if any(s in text for s in ('permission denied', 'operation not permitted', 'eacces', 'eperm')):return 'permission'
    if any(s in text for s in ('invalid_api_key', 'unauthorized', 'authentication', '401')):return 'authentication'
    if any(s in text for s in ('timed out', 'timeout', '超时', 'connection reset', 'connection refused',
                               'network', 'dns', 'stream disconnected', 'connection closed')):return 'network'
    return 'unknown'


def read_json(path, default):
    if path.is_symlink():raise ValueError('工具数据不能是符号链接')
    return json.loads(path.read_text()) if path.exists() else default


def _connect(path):
    if path.is_symlink() or not path.is_file():raise ValueError('未找到可读取的会话数据库')
    db = sqlite3.connect(path.as_uri()+'?mode=ro', uri=True, timeout=2)
    db.row_factory = sqlite3.Row
    return db


def _paths(home, tid, stored):
    def candidate(path):
        return (not path.is_symlink() and path.is_file() and
                any(path.resolve().is_relative_to((home/name).resolve()) and not (home/name).is_symlink()
                    for name in ('sessions', 'archived_sessions')))
    def matches(path):
        with path.open('rb') as stream:header = json.loads(stream.readline())
        payload = header.get('payload', {})
        return header.get('type') == 'session_meta' and payload.get('id', payload.get('session_id')) == tid
    paths = []
    search_incomplete = False
    if stored:
        path = Path(stored)
        if not path.is_absolute():path = home/path
        if candidate(path) and matches(path):paths.append(path)
    # The DB path may be stale after archiving. Include same-ID copies as well.
    for name in ('sessions', 'archived_sessions'):
        base = home/name
        if base.is_symlink():raise ValueError('会话目录不能是符号链接')
        for path in base.rglob('*.jsonl'):
            try:
                if path in paths or not candidate(path):continue
                if matches(path):paths.append(path)
            except (ValueError, UnicodeError):continue
            except OSError:
                # We cannot attribute an unrelated unreadable file to this thread.
                # Known target paths and conventional same-ID names still fail visibly.
                if path.name == tid+'.jsonl' or path.name.endswith('-'+tid+'.jsonl'):raise
                search_incomplete = True
    return paths, search_incomplete


def diagnose_thread(home, tid):
    home = Path(home or Path.home()/'.codex').expanduser().absolute()
    if home.is_symlink():raise ValueError('Codex 数据目录不能是符号链接')
    with closing(_connect(home/'state_5.sqlite')) as db:
        row = db.execute('select * from threads where id=?', (tid,)).fetchone()
    if row is None:raise ValueError('找不到原会话')
    title = row['name'] if 'name' in row.keys() and row['name'] else row['title']
    category = 'unavailable'; recent = []; evidence = '未读取到最近运行记录。'
    history = home/'thread_history_1.sqlite'
    if history.exists():
        try:
            with closing(_connect(history)) as db:
                columns = {r['name'] for r in db.execute('pragma table_info(thread_turns)')}
                if {'thread_id', 'status', 'error_json', 'rollout_ordinal'} <= columns:
                    recent = list(db.execute('select status,error_json from thread_turns where thread_id=? '
                                            'order by rollout_ordinal desc limit 5', (tid,)))
        except (sqlite3.Error, ValueError):evidence = '运行记录暂不可读或数据库版本不支持；未修改文件。'
    if recent:
        status = recent[0]['status']
        if status in ('completed', 'inProgress', 'in_progress'):
            category = 'no_recent_failure'
            evidence = '最近一轮已完成。' if status == 'completed' else '最近一轮仍在运行；请勿重复发送。'
        elif recent[0]['error_json']:
            category = classify_error(recent[0]['error_json'])
            repeated = sum(bool(r['error_json']) and classify_error(r['error_json']) == category for r in recent)
            evidence = '最近一轮失败；最近 '+str(len(recent))+' 轮中有 '+str(repeated)+' 轮属于此类。'
        else:
            category = 'unknown'; evidence = '最近一轮未正常完成，但没有详细错误记录。'
    state = 'unchecked'; format_summary = '未找到本机会话文件，未检查历史格式。'; repair_records = 0
    try:
        paths, search_incomplete = _paths(home, tid, row['rollout_path'] if 'rollout_path' in row.keys() else None)
        if paths:
            repair_records = sum(len(plan_repairs(path)) for path in paths)
            state = 'repairable' if repair_records else 'local_checked'
            format_summary = ('面向 GPT 的格式检查：发现 '+str(repair_records)+' 条可安全修复的推理记录。' if repair_records else
                              '面向 GPT 的本地格式检查通过：未发现已知推理格式冲突；尚未验证模型回复。')
        if search_incomplete:format_summary += ' 部分其他文件无法读取，仅检查已找到的会话文件。'
    except ValueError:
        state = 'needs_continuation'; format_summary = '存在无法安全自动修复的历史格式，建议兼容接续。'
    except (OSError, sqlite3.Error):
        format_summary = '无法读取本机会话文件，请检查文件访问权限。'
        if category == 'unavailable':category = 'permission'
    if category == 'history_format':state = 'needs_continuation'
    label, advice = ADVICE[category]
    return {'id': tid, 'title': title, 'state': state, 'category': category, 'summary': label,
            'evidence': evidence, 'advice': advice, 'format_summary': format_summary,
            'repair_records': repair_records, 'checked_at': time.time(),
            'provider': row['model_provider'], 'model': row['model']}

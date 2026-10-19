import argparse
import json
import sys
sys.dont_write_bytecode = True
from switcher.core import Switcher
from switcher.permissions import check_permissions, require_permissions, PermissionCheckError

PAYLOAD_COMMANDS=['switch','restore','settings','save-key','bridge','diagnose','ignore-attention','repair-formats','repair-deepseek','web-search']


def payload():
    if sys.stdin.isatty():raise ValueError('没有收到参数：请在本工具界面里操作，不要直接双击打开后端脚本')
    raw=sys.stdin.read()
    if not raw.strip():raise ValueError('没有收到参数：请重新点一次按钮；没有修改任何文件')
    try:data=json.loads(raw)
    except ValueError:raise ValueError('请求参数无法解析，请重新点一次按钮；没有修改任何文件')
    if not isinstance(data,dict):raise ValueError('请求参数无法解析，请重新点一次按钮；没有修改任何文件')
    return data


def require(data,name,what):
    value=data.get(name)
    if not isinstance(value,str) or not value:raise ValueError('没有收到'+what+'，请重新操作；没有修改任何文件')
    return value


def main():
    p=argparse.ArgumentParser()
    p.add_argument('--home')
    p.add_argument('command',choices=['permissions','recovery-status','diagnose','status','startup','live-status','switch','backup','restore','cleanup','settings','save-key','bridge','ignore-attention','clear-attention-ignores','repair','repair-formats','repair-deepseek','web-search'])
    args=p.parse_args()
    data=payload() if args.command in PAYLOAD_COMMANDS else {}
    # Permission reporting must work even when Switcher cannot create its data directory.
    if args.command=='permissions':
        print(json.dumps({'ok':True,'result':check_permissions(args.home)},ensure_ascii=False))
        return
    if args.command=='recovery-status':
        print(json.dumps({'ok':True,'result':Switcher.recovery_status(args.home)},ensure_ascii=False))
        return
    if args.command=='diagnose':
        from switcher.diagnostics import diagnose_thread
        print(json.dumps({'ok':True,'result':diagnose_thread(args.home,require(data,'id','会话 ID'))},ensure_ascii=False))
        return
    if args.command!='live-status':require_permissions(args.home, allow_missing_config=args.command=='restore')
    app=Switcher(args.home)
    if args.command in ['status','startup']:
        if args.command=='startup' and app.settings()['auto_cleanup']:app.cleanup()
        result=app.status()
    elif args.command=='live-status':result=app.runtime_status()
    elif args.command=='switch':result=app.switch(require(data,'provider','服务商'),require(data,'model','模型'),data.get('effort','high'),days=int(data.get('days',30)))
    elif args.command=='backup':result=app.backup()
    elif args.command=='restore':result=app.restore(require(data,'id','备份 ID'))
    elif args.command=='repair':result=app.repair()
    elif args.command=='repair-formats':result=app.repair_compatibility(data.get('id'))
    elif args.command=='repair-deepseek':result=app.repair_deepseek(data.get('id'))
    elif args.command=='web-search':result=app.set_web_search(data.get('enabled'))
    elif args.command=='cleanup':result={'removed':len(app.cleanup())}
    elif args.command=='settings':result=app.save_settings(int(data.get('keep',0)),int(data.get('days',0)),data.get('auto_cleanup',True))
    elif args.command=='bridge':result=app.bridge(require(data,'id','会话 ID'))
    elif args.command=='ignore-attention':result=app.ignore_attention(require(data,'id','会话 ID'))
    elif args.command=='clear-attention-ignores':result=app.clear_attention_ignores()
    else:result=app.save_key(data.get('key',''))
    print(json.dumps({'ok':True,'result':result},ensure_ascii=False))


if __name__=='__main__':
    try:main()
    except Exception as exc:
        # Known validation messages are safe; never print arbitrary config parser contents or secrets.
        name=type(exc).__name__
        if isinstance(exc,PermissionCheckError):
            print(json.dumps({'ok':False,'error':str(exc),'permissions':exc.report},ensure_ascii=False))
            sys.exit(1)
        if 'TOML' in name:
            detail=' '.join(str(exc).split())[:160]
            message='配置文件格式无效，已停止操作。'+(('（'+detail+'）') if detail else '')
        elif isinstance(exc,ValueError) and not isinstance(exc,json.JSONDecodeError):message=str(exc)
        elif isinstance(exc,OSError):message='读写 Codex 配置或备份目录时被系统拒绝（'+name+'），已停止操作，原会话未改动。请检查 ~/.codex 的权限后重试。'
        else:message='操作失败（'+name+'），原会话保留。请检查磁盘空间和文件权限。'
        print(json.dumps({'ok':False,'error':message},ensure_ascii=False));sys.exit(1)

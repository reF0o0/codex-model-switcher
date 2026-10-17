import argparse
import json
import sys
sys.dont_write_bytecode = True
from switcher.core import Switcher


def main():
    p=argparse.ArgumentParser()
    p.add_argument('--home')
    p.add_argument('command',choices=['status','startup','switch','backup','restore','cleanup','settings','save-key','bridge'])
    args=p.parse_args()
    data=json.load(sys.stdin) if not sys.stdin.isatty() and args.command in ['switch','restore','settings','save-key','bridge'] else {}
    app=Switcher(args.home)
    if args.command in ['status','startup']:
        if args.command=='startup' and app.settings()['auto_cleanup']:app.cleanup()
        result=app.status()
    elif args.command=='switch':result=app.switch(data['provider'],data['model'],data.get('effort','high'))
    elif args.command=='backup':result=app.backup()
    elif args.command=='restore':result=app.restore(data['id'])
    elif args.command=='cleanup':result={'removed':len(app.cleanup())}
    elif args.command=='settings':result=app.save_settings(int(data['keep']),int(data['days']),data.get('auto_cleanup',True))
    elif args.command=='bridge':result=app.bridge(data['id'])
    else:result=app.save_key(data['key'])
    print(json.dumps({'ok':True,'result':result},ensure_ascii=False))


if __name__=='__main__':
    try:main()
    except Exception as exc:
        # Known validation messages are safe; never print arbitrary config parser contents or secrets.
        message=str(exc) if isinstance(exc,ValueError) and not isinstance(exc,json.JSONDecodeError) else '操作失败（'+type(exc).__name__+'），原会话保留。请检查磁盘空间和文件权限。'
        if 'TOML' in type(exc).__name__:message='配置文件格式无效，已停止操作。'
        print(json.dumps({'ok':False,'error':message},ensure_ascii=False));sys.exit(1)

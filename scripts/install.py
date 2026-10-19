"""Install releases only when signing identity is stable; preserve settings and the previous app."""
import argparse
from datetime import datetime
import json
import os
from pathlib import Path
import plistlib
import shutil
import signal
import subprocess
import tempfile
import time

from signing import BUNDLE_ID, requirement_for, validate_update, validate_requirement


def install(archive, target, migrate_legacy=False):
    if (Path.home()/'.codex/provider-switcher/pending.json').exists():
        raise ValueError('检测到未完成的切换，安装已停止。')
    with tempfile.TemporaryDirectory(prefix='switcher-install-',dir='/private/tmp') as temp:
        subprocess.run(['/usr/bin/ditto','-x','-k',str(archive),temp],check=True,capture_output=True)
        candidate=Path(temp)/target.name
        subprocess.run(['/usr/bin/codesign','--verify','--deep','--strict',str(candidate)],check=True,capture_output=True)
        info=plistlib.loads((candidate/'Contents/Info.plist').read_bytes())
        if info['CFBundleIdentifier']!=BUNDLE_ID:raise ValueError('安装包标识符不匹配，未安装。')
        requirement=validate_requirement(requirement_for(candidate))
        if target.exists():
            current=plistlib.loads((target/'Contents/Info.plist').read_bytes())
            if current['CFBundleIdentifier']!=BUNDLE_ID:raise ValueError('现有应用标识符不匹配，未替换。')
            validate_update(requirement_for(target),requirement,migrate_legacy)
        executable=str(target/'Contents/MacOS/ProviderSwitcher')
        processes=[line.strip().split(None,2) for line in subprocess.check_output(['ps','-axo','pid=,ppid=,comm='],text=True).splitlines()]
        for row in processes:
            if len(row)!=3 or row[2]!=executable:continue
            pid=int(row[0])
            deadline=time.monotonic()+5
            while any(len(p)==3 and int(p[1])==pid for p in processes):
                if time.monotonic()>=deadline:
                    raise ValueError('切换器仍有操作在运行，未替换应用。请等当前操作结束后重试。')
                time.sleep(0.2)
                processes=[line.strip().split(None,2) for line in subprocess.check_output(
                    ['ps','-axo','pid=,ppid=,comm='],text=True).splitlines()]
            os.kill(pid,signal.SIGTERM)
            for _ in range(60):
                try:os.kill(pid,0)
                except ProcessLookupError:break
                time.sleep(0.05)
            else:raise ValueError('旧版尚未退出，未替换应用。')
        backup=Path.home()/'Library/Application Support/Codex Model Switcher/Previous Apps'/datetime.now().strftime('%Y%m%d-%H%M%S-%f')
        backup.mkdir(parents=True,mode=0o700)
        previous=backup/target.name
        if target.exists():shutil.move(str(target),str(previous))
        try:
            subprocess.run(['/usr/bin/ditto','--norsrc','--noextattr',str(candidate),str(target)],check=True,capture_output=True)
            subprocess.run(['/usr/bin/codesign','--verify','--deep','--strict',str(target)],check=True,capture_output=True)
            if requirement_for(target)!=requirement:raise ValueError('安装后身份核对失败。')
        except BaseException:
            if target.exists():shutil.move(str(target),str(backup/'failed-install.app'))
            if previous.exists():shutil.move(str(previous),str(target))
            raise
        return {'version':info['CFBundleShortVersionString'],'previous_app':str(previous),
                'settings_preserved':True,'legacy_migration':migrate_legacy}


if __name__=='__main__':
    parser=argparse.ArgumentParser()
    parser.add_argument('--migrate-legacy',action='store_true',help='Only after the user is ready for the one-time transition from ad-hoc signing')
    args=parser.parse_args()
    try:
        repo=Path(__file__).resolve().parents[1]
        result=install(repo/'dist/codex-model-switcher-macos-arm64.zip',Path('/Applications/Codex 模型切换器.app'),args.migrate_legacy)
        print(json.dumps(result,ensure_ascii=False))
    except (ValueError,OSError,subprocess.CalledProcessError) as exc:
        detail=str(exc).strip()
        print(json.dumps({'ok':False,'error':'安装未完成，现有应用已保留或恢复。请检查固定签名与操作状态；首次迁移请先阅读 docs/signing.md。'+
                          ((' 原因：'+detail[:300]) if detail else '')},ensure_ascii=False))
        raise SystemExit(1)

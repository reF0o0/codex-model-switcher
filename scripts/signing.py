"""Keep one local signing identity across releases; never alter system trust or privacy grants."""
import argparse
import json
from pathlib import Path
import re
import secrets
import shlex
import subprocess
import tempfile

BUNDLE_ID = 'io.github.ref0o0.codex-model-switcher'
SIGNING_HOME = Path.home()/'Library/Application Support/Codex Model Switcher/Signing'


def run(args):
    # Password arguments are never included in errors or printed command lines.
    try:result = subprocess.run([str(a) for a in args],capture_output=True,text=True,timeout=30)
    except (OSError,subprocess.TimeoutExpired):raise ValueError('固定签名命令无法完成；现有应用和签名材料保留。') from None
    if result.returncode:raise ValueError('固定签名步骤失败（'+Path(args[0]).name+'）；现有应用和签名材料保留。')
    return result.stdout


def _private_file(path, content):
    with path.open('x') as stream:stream.write(content)
    path.chmod(0o600)


def load_identity(root):
    paths=[root/name for name in ('identity.json','identity.cer','identity.keychain-db','keychain-password')]
    if root.is_symlink() or any(p.is_symlink() or not p.is_file() for p in paths):
        raise ValueError('签名材料不完整或路径异常，已保留原文件；请恢复原材料，不要重新生成身份。')
    data=json.loads(paths[0].read_text())
    identity=data.get('identity','')
    if not re.fullmatch('[A-F0-9]{40}',identity):raise ValueError('签名身份记录无效，原文件保留。')
    actual=run(['/usr/bin/openssl','x509','-inform','DER','-in',paths[1],'-noout','-fingerprint','-sha1'])
    if actual.strip().split('=')[-1].replace(':','')!=identity:
        raise ValueError('签名证书身份已变化，已停止；请恢复原签名材料。')
    return data


def prepare_identity(root):
    root=Path(root)
    if root.exists():
        load_identity(root)
        return
    root.mkdir(parents=True,mode=0o700)
    root.chmod(0o700)
    password=secrets.token_urlsafe(40)
    _private_file(root/'keychain-password',password)
    keychain=root/'identity.keychain-db'
    original=shlex.split(run(['/usr/bin/security','list-keychains','-d','user']))
    # All exported key material stays in a private temporary directory and is removed after import.
    with tempfile.TemporaryDirectory(prefix='.setup-',dir=root) as temp:
        work=Path(temp)
        config=work/'openssl.conf'
        config.write_text('[req]\ndistinguished_name=dn\nx509_extensions=ext\nprompt=no\n'
                          '[dn]\nCN=Codex Model Switcher Local Signing\n'
                          '[ext]\nbasicConstraints=critical,CA:FALSE\n'
                          'keyUsage=critical,digitalSignature\nextendedKeyUsage=critical,codeSigning\n')
        run(['/usr/bin/openssl','req','-new','-newkey','rsa:3072','-x509','-sha256','-nodes',
             '-days','3650','-config',config,'-keyout',work/'key.pem','-out',work/'cert.pem'])
        (work/'key.pem').chmod(0o600)
        run(['/usr/bin/openssl','pkcs12','-export','-inkey',work/'key.pem','-in',work/'cert.pem',
             '-out',work/'identity.p12','-passout','pass:'+password])
        (work/'identity.p12').chmod(0o600)
        run(['/usr/bin/openssl','x509','-in',work/'cert.pem','-outform','DER','-out',root/'identity.cer'])
        (root/'identity.cer').chmod(0o600)
        try:
            run(['/usr/bin/security','create-keychain','-p',password,keychain])
            run(['/usr/bin/security','unlock-keychain','-p',password,keychain])
            run(['/usr/bin/security','import',work/'identity.p12','-k',keychain,'-P',password,'-T','/usr/bin/codesign'])
            run(['/usr/bin/security','set-key-partition-list','-S','apple-tool:,apple:,codesign:',
                 '-s','-k',password,keychain])
        finally:
            # Creating a keychain can add it to the user's global search list. Leave that list unchanged.
            run(['/usr/bin/security','list-keychains','-d','user','-s',*original])
        keychain.chmod(0o600)
        fingerprint=run(['/usr/bin/openssl','x509','-in',work/'cert.pem','-noout','-fingerprint','-sha1'])
        identity=fingerprint.strip().split('=')[-1].replace(':','')
        _private_file(root/'identity.json',json.dumps({'version':1,'identity':identity})+'\n')


def identity_ready(root):
    data=load_identity(root)
    run(['/usr/bin/security','unlock-keychain','-p',(root/'keychain-password').read_text(),root/'identity.keychain-db'])
    listing=run(['/usr/bin/security','find-identity','-v','-p','codesigning',root/'identity.keychain-db'])
    return data['identity'] in listing


def requirement_for(app):
    result=subprocess.run(['/usr/bin/codesign','-d','-r-',str(app)],capture_output=True,text=True,timeout=15)
    if result.returncode:raise ValueError('应用签名无法读取，未替换现有应用。')
    text=result.stdout+'\n'+result.stderr
    for line in text.splitlines():
        if 'designated =>' in line:return line.split('designated =>',1)[1].strip()
    raise ValueError('应用缺少签名身份，未替换现有应用。')


def validate_requirement(requirement):
    if 'cdhash' in requirement:raise ValueError('临时签名不能用于更新：每次构建都会改变权限身份。')
    if ('identifier "'+BUNDLE_ID+'"' not in requirement or
            not re.search(r'(?:anchor|certificate\s+(?:leaf|0))\s*(?:=\s*)?H"[a-fA-F0-9]{40}"',requirement)):
        raise ValueError('更新包必须绑定固定证书身份，不能只匹配应用名称或标识符。')
    return requirement


def pin_requirement(root, requirement):
    path=root/'requirement.txt'
    if path.is_symlink():raise ValueError('签名身份记录路径异常，已停止。')
    if path.exists():
        if path.read_text().strip()!=requirement:raise ValueError('更新的签名身份与原版本不同，已停止更新。')
    else:_private_file(path,requirement+'\n')


def validate_update(previous, candidate, migrate_legacy=False):
    validate_requirement(candidate)
    if previous==candidate:return
    if 'cdhash' in previous and migrate_legacy:return
    if 'cdhash' in previous:
        raise ValueError('旧版是临时签名；首次迁移固定身份可能需要一次权限确认。请等用户方便时再迁移，现有应用保留。')
    raise ValueError('更新的签名身份与已安装版本不同，已停止更新，现有应用和授权保留。')


def sign_app(root, app):
    load_identity(root)
    if not identity_ready(root):
        raise ValueError('固定签名证书尚未完成一次性系统信任设置；未生成更新包，现有应用不变。证书：'+str(root/'identity.cer'))
    identity=load_identity(root)['identity']
    keychain=str(root/'identity.keychain-db')
    original=shlex.split(run(['/usr/bin/security','list-keychains','-d','user']))
    # codesign requires the identity's keychain on the search list even with
    # --keychain (see codesign(1), SIGNING IDENTITIES). Keep this change transient.
    try:
        if keychain not in original:
            run(['/usr/bin/security','list-keychains','-d','user','-s',*original,keychain])
        run(['/usr/bin/codesign','--force','--sign',identity,'--keychain',keychain,
             '--timestamp=none',app])
    finally:
        if keychain not in original:
            run(['/usr/bin/security','list-keychains','-d','user','-s',*original])
    run(['/usr/bin/codesign','--verify','--deep','--strict',app])
    requirement=validate_requirement(requirement_for(app))
    pin_requirement(root,requirement)


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('command',choices=['prepare','status','sign'])
    parser.add_argument('app',nargs='?')
    args=parser.parse_args()
    if args.command=='prepare':prepare_identity(SIGNING_HOME)
    if args.command=='sign':
        if not args.app:raise ValueError('缺少需要签名的应用路径。')
        sign_app(SIGNING_HOME,Path(args.app))
    else:
        print(json.dumps({'prepared':True,'ready':identity_ready(SIGNING_HOME),
                          'certificate':str(SIGNING_HOME/'identity.cer'),
                          'note':'不修改系统信任或隐私权限；完成一次性代码签名证书设置后复用同一身份。'},ensure_ascii=False))


if __name__=='__main__':
    try:main()
    except (ValueError,OSError) as exc:
        print(json.dumps({'ok':False,'error':str(exc)},ensure_ascii=False))
        raise SystemExit(1)

"""Installed Codex app-server protocol, on local stdio only."""
import json
import os
from pathlib import Path
import selectors
import subprocess
import time
from collections import deque


class RpcTimeoutError(TimeoutError):
    """No event arrived before this receive call's deadline."""


class Rpc:
    def __init__(self, home):
        choices=[Path('/Applications/ChatGPT.app/Contents/Resources/codex-cli/CodexCLI.app/Contents/MacOS/codex'),
                 Path('/Applications/Codex.app/Contents/Resources/codex')]
        cli=next((p for p in choices if p.is_file()),None)
        if cli is None:raise ValueError('找不到安装的 Codex CLI')
        self.process=subprocess.Popen([str(cli),'app-server','--stdio'],stdin=subprocess.PIPE,
                                      stdout=subprocess.PIPE,stderr=subprocess.DEVNULL,bufsize=0,
                                      env={**os.environ,'CODEX_HOME':str(home)})
        self.selector=selectors.DefaultSelector()
        self.selector.register(self.process.stdout,selectors.EVENT_READ)
        self.buffer=b''
        self.ident=0
        self.events=deque()
        try:
            self.call('initialize',{'clientInfo':{'name':'codex-provider-switcher','version':'1.0'},
                                    'capabilities':{'experimentalApi':True}})
            self.send({'method':'initialized'})
        except BaseException:
            self.close();raise

    def send(self,data):
        self.process.stdin.write((json.dumps(data,ensure_ascii=False)+'\n').encode())
        self.process.stdin.flush()

    def receive(self,timeout=45):
        if self.events:return self.events.popleft()
        return self._receive(timeout)

    def _receive(self,timeout=45):
        deadline=time.monotonic()+timeout
        while time.monotonic()<deadline:
            if b'\n' in self.buffer:
                line,self.buffer=self.buffer.split(b'\n',1)
                return json.loads(line)
            if self.selector.select(1):
                chunk=os.read(self.process.stdout.fileno(),65536)
                if not chunk:raise ValueError('Codex 后端已退出')
                self.buffer+=chunk
        raise RpcTimeoutError('等待 Codex 后端超时；原会话保留')

    def call(self,method,params):
        self.ident+=1
        self.send({'id':self.ident,'method':method,'params':params})
        deadline=time.monotonic()+45
        while time.monotonic()<deadline:
            data=self._receive(max(1,deadline-time.monotonic()))
            if data.get('id')==self.ident:
                if 'error' in data:
                    err=ValueError('Codex 会话接口拒绝操作（'+str(data['error'].get('code'))+'）；原会话保留')
                    err.rpc_error=data['error']
                    raise err
                return data['result']
            if 'method' in data and 'id' not in data:self.events.append(data)
        raise ValueError('Codex 接口超时')

    def close(self):
        self.process.terminate()
        try:self.process.wait(timeout=5)
        except subprocess.TimeoutExpired:self.process.kill();self.process.wait()
        self.selector.close()
        for stream in [self.process.stdin,self.process.stdout]:
            if stream:stream.close()

    def __enter__(self):return self
    def __exit__(self,*args):self.close()

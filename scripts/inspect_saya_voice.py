"""WSL-side audit/cleanup limited to this workspace's random voice tokens."""
import argparse,json,os,re,signal,time
from pathlib import Path


def scan(root,known=()):
    tokens={t for t in known if isinstance(t,str) and re.fullmatch('[a-f0-9]{32}',t)}
    expected=set()
    for path in (root/'.runtime').glob('qwen-omni-*.pid'):
        token=path.stem[len('qwen-omni-'):]
        if re.fullmatch('[a-f0-9]{32}',token):
            tokens.add(token)
            try:expected.add(int(path.read_text()))
            except (OSError,ValueError):pass
    rows=[];uncertain=[]
    for path in Path('/proc').glob('[0-9]*'):
        try:
            fields=(path/'stat').read_text().rsplit(')',1)[1].split()
            if fields[0]=='Z':continue
            env=(path/'environ').read_bytes().split(b'\0')
            token=next((x.split(b'=',1)[1].decode() for x in env if x.startswith(b'SAYA_OMNI_TOKEN=')),None)
            if token in tokens:rows.append({'pid':int(path.name),'birth':fields[19],'token':token})
        except (OSError,ValueError,UnicodeError):
            if int(path.name) in expected and path.exists():
                # Old numeric pid files can now point at WSL's root-owned
                # Relay or an unrelated process. Never mistake PID reuse for
                # an unreadable live project worker.
                try:
                    command=(path/'cmdline').read_bytes()
                    if str(root).encode() in command:uncertain.append(int(path.name))
                except OSError:uncertain.append(int(path.name))
    return {'processes':rows,'uncertain':uncertain,'tokens':sorted(tokens)}


def same(row):
    try:
        path=Path('/proc')/str(row['pid']);fields=(path/'stat').read_text().rsplit(')',1)[1].split()
        return fields[0]!='Z' and fields[19]==row['birth'] and ('SAYA_OMNI_TOKEN='+row['token']).encode() in (path/'environ').read_bytes().split(b'\0')
    except (OSError,ValueError):return False


def cleanup(root,known):
    before=scan(root,known)
    for row in before['processes']:
        if same(row):
            try:os.kill(row['pid'],signal.SIGTERM)
            except ProcessLookupError:pass
    deadline=time.monotonic()+8
    while time.monotonic()<deadline and any(same(x) for x in before['processes']):time.sleep(.1)
    for row in before['processes']:
        if same(row):
            try:os.kill(row['pid'],signal.SIGKILL)
            except ProcessLookupError:pass
    return scan(root,before['tokens'])


def main():
    p=argparse.ArgumentParser();p.add_argument('--root',type=Path,required=True);p.add_argument('--stop',action='store_true');a=p.parse_args()
    request=json.load(__import__('sys').stdin);known=request.get('tokens',[])
    if not isinstance(known,list) or len(known)>4096:raise ValueError('invalid token inventory')
    print(json.dumps(cleanup(a.root,known) if a.stop else scan(a.root,known)))


if __name__=='__main__':main()

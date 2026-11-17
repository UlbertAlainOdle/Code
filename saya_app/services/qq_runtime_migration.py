"""Stage an explicit installed-QQ binding without changing active configuration.

The official Shell loader is retained in private workspace runtime. No files in
the existing QQ installation are written, patched, moved, or removed.
"""
import hashlib,json,re,stat,zipfile
from pathlib import Path,PurePosixPath
from uuid import uuid4

SHELL_SHA256='6801f93e533846cea05f4f531874a0e84371ab2331c5585b57cd7e3b869cf96b'
LOADER_FILES={'NapCatWinBootMain.exe','NapCatWinBootHook.dll','loadNapCat.js','qqnt.json','napcat.mjs'}


def stage_external_runtime(root,qq_exe):
    root=Path(root).resolve();base=root/'.runtime/napcat';state=base/'install.json'
    original=state.read_bytes();record=json.loads(original.decode('utf-8-sig'))
    exe=Path(qq_exe)
    if not exe.is_absolute() or str(exe).startswith(('\\\\','//')) or exe.name.casefold()!='qq.exe':raise ValueError('absolute local QQ.exe required')
    exe=exe.resolve()
    if not exe.is_file():raise ValueError('installed QQ executable missing')
    install=exe.parent;version=json.loads((install/'versions/config.json').read_text('utf-8-sig')).get('curVersion','')
    if not re.fullmatch(r'\d+\.\d+\.\d+-\d+',version):raise ValueError('installed QQ version invalid')
    app=install/'versions'/version/'resources/app'
    for name in ['wrapper.node','major.node','application.asar','application.json']:
        if not (app/name).is_file():raise ValueError('installed QQ resources incomplete')
    archive=base/'NapCat.Shell.v4.18.41.verified.zip'
    if hashlib.sha256(archive.read_bytes()).hexdigest()!=SHELL_SHA256:raise ValueError('verified Shell archive mismatch')
    directory=base/('installed-qq-'+version+'-'+uuid4().hex[:8]);assert not directory.exists()
    with zipfile.ZipFile(archive) as bundle:
        entries=bundle.infolist()
        if len(entries)>2000 or sum(x.file_size for x in entries)>256*1024*1024:raise ValueError('Shell archive exceeds bound')
        for entry in entries:
            path=PurePosixPath(entry.filename)
            if path.is_absolute() or '..' in path.parts or '\\' in entry.filename or ':' in entry.filename or stat.S_ISLNK(entry.external_attr>>16):
                raise ValueError('Shell archive unsafe entry')
        if not LOADER_FILES.issubset({x.filename for x in entries}):raise ValueError('Shell bootstrap incomplete')
        directory.mkdir()
        bundle.extractall(directory)
    candidate=record|{'runtime_kind':'external_shell','install_dir':str(install),'qq_version':version,
        'runtime_dir':str(directory),'qq_exe_sha256':hashlib.sha256(exe.read_bytes()).hexdigest(),
        'qq_instance_id':uuid4().hex,'runtime_hashes':{n:hashlib.sha256((directory/n).read_bytes()).hexdigest() for n in LOADER_FILES}}
    # Validate before any active pointer switch, using the same runtime loader.
    from .qq_napcat import _external_install
    _external_install(base.resolve(),candidate)
    return {'candidate':candidate,'previous_install_sha256':hashlib.sha256(original).hexdigest(),
        'QQ_wrapper_sha256':hashlib.sha256((app/'wrapper.node').read_bytes()).hexdigest(),
        'Shell_archive_sha256':SHELL_SHA256,'writes_to_existing_QQ':0}


def activate_external_runtime(root,staged,*,confirmed=False):
    """CAS switch after staging; preserve bot identity, profile, and other metadata.

    Caller owns the maintenance/drain window. This never stops a process, logs
    in, enables supervision, or restores older configuration defaults.
    """
    if confirmed is not True:raise ValueError('explicit installed-QQ migration confirmation required')
    from .qq_lifecycle import InstanceLease
    from .qq_napcat import load_install,_external_install,discover
    from .state_storage import atomic_save_json
    root=Path(root).resolve();base=root/'.runtime/napcat';state=base/'install.json'
    with InstanceLease(root/'.runtime/qq-service/napcat-operation.lock'):
        original=state.read_bytes()
        if hashlib.sha256(original).hexdigest()!=staged.get('previous_install_sha256'):
            raise ValueError('installation changed since staging; original retained')
        previous=json.loads(original.decode('utf-8-sig'));candidate=staged['candidate']
        allowed={'runtime_kind','install_dir','qq_version','runtime_dir','qq_exe_sha256','qq_instance_id','runtime_hashes'}
        if any(candidate.get(k)!=v for k,v in previous.items() if k not in allowed) or any(k not in previous and k not in allowed for k in candidate):
            raise ValueError('migration cannot change identity/profile/other installation metadata')
        _external_install(base.resolve(),candidate)
        found=discover(load_install(root))
        if found['members'] or found['uncertain'] or found['foreign']:
            raise ValueError('old managed runtime must be stopped before pointer migration')
        atomic_save_json(state,candidate)
    return load_install(root)

#!/usr/bin/env python3
"""Sign, stage and atomically switch the user-owned Mavi app; retain rollback."""
import hashlib,json,os,plistlib,shutil,subprocess,sys,time,uuid
from pathlib import Path
ROOT=Path.home()/'Library/Application Support/Mavi/Updates'
INSTALLED=Path.home()/'Applications/Mavi.app'
BUNDLE_ID='app.mavi.desktop'
EXECUTABLE='Mavi'
os.umask(0o077)
def digest(p):return hashlib.sha256(p.read_bytes()).hexdigest()
def validate_app(app):
    if app.is_symlink():raise ValueError('App cannot be a symlink')
    info=plistlib.loads((app/'Contents/Info.plist').read_bytes())
    if info.get('CFBundleIdentifier')!=BUNDLE_ID or info.get('CFBundleExecutable')!=EXECUTABLE:raise ValueError('Incorrect application identity')
    subprocess.run(['/usr/bin/codesign','--verify','--strict',str(app)],check=True,capture_output=True)
def hashes(app):return {str(p.relative_to(app)):digest(p) for p in app.rglob('*') if p.is_file()}
def prepare(action,run,installed,pid):
    if Path(installed).resolve()!=INSTALLED.resolve():raise ValueError('Unexpected installation location')
    validate_app(INSTALLED)
    if action=='rollback':
        previous=json.loads((ROOT/'last-install.json').read_text()); candidate=Path(previous['backup'])
        if not candidate.resolve().is_relative_to(ROOT.resolve()):raise ValueError('Invalid rollback path')
        validate_app(candidate)
    else:
        run=Path(run).resolve(strict=True)
        if run.parent!=ROOT.resolve():raise ValueError('Invalid candidate directory')
        manifest=json.loads((run/'manifest.json').read_text()); candidate=run/'Mavi.app'
        if not manifest.get('ready') or hashes(candidate)!=manifest['files']:raise ValueError('Candidate changed since tests; build again')
        validate_app(candidate)
    job=ROOT/('install-'+uuid.uuid4().hex);job.mkdir(parents=True)
    stage=job/'Mavi.app';shutil.copytree(candidate,stage,symlinks=False)
    subprocess.run(['/usr/bin/codesign','--force','--sign','-','--timestamp=none',str(stage)],check=True,capture_output=True,timeout=300)
    validate_app(stage)
    ticket={'installed':str(INSTALLED),'stage':str(stage),'backup':str(job/'Previous.app'),'rollback':action=='rollback','parent_pid':int(pid),'hashes':hashes(stage),'health':str(job/'healthy'),'job':str(job)}
    path=job/'ticket.json';path.write_text(json.dumps(ticket));return path
def start(app,health,log):
    return subprocess.Popen([str(app/'Contents/MacOS/Mavi'),'--update-health-check',str(health)],stdin=subprocess.DEVNULL,stdout=log,stderr=log,start_new_session=True)
def commit(ticket_path):
    ticket_path=Path(ticket_path).resolve(strict=True)
    if ticket_path.parent.parent!=ROOT.resolve():raise ValueError('Invalid install ticket')
    data=json.loads(ticket_path.read_text());job=ticket_path.parent
    target=Path(data['installed']);stage=Path(data['stage']);backup=Path(data['backup']);health=Path(data['health'])
    if target!=INSTALLED or stage!=job/'Mavi.app' or backup!=job/'Previous.app' or health!=job/'healthy':raise ValueError('Ticket paths are invalid')
    if hashes(stage)!=data['hashes']:raise ValueError('Staged app changed')
    validate_app(stage)
    deadline=time.monotonic()+30
    while time.monotonic()<deadline:
        try:os.kill(data['parent_pid'],0)
        except ProcessLookupError:break
        time.sleep(.2)
    else:raise RuntimeError('Mavi did not quit; installed app left intact')
    target.rename(backup)
    proc=None
    try:
        shutil.copytree(stage,target)
        validate_app(target)
        with (job/'launch.log').open('ab') as log:
            proc=start(target,health,log)
            deadline=time.monotonic()+30
            while time.monotonic()<deadline:
                if proc.poll() is not None:raise RuntimeError('New app exited during launch')
                if health.exists() or data.get('rollback',False):
                    time.sleep(3)
                    if proc.poll() is not None:raise RuntimeError('New app exited after startup')
                    (ROOT/'last-install.json').write_text(json.dumps({'backup':str(backup),'installed':str(target),'job':str(job)}))
                    (job/'result.json').write_text(json.dumps({'success':True}));return
                time.sleep(.25)
            raise RuntimeError('New app did not report a ready window')
    except Exception as e:
        if proc is not None and proc.poll() is None:proc.terminate();proc.wait(timeout=10)
        if target.exists():shutil.rmtree(target)
        backup.rename(target)
        subprocess.Popen(['/usr/bin/open','-n',str(target)],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
        (job/'result.json').write_text(json.dumps({'success':False,'restored':True,'error':str(e)}));raise
if __name__=='__main__':
    try:
        action=sys.argv[1]
        if action=='commit':commit(sys.argv[2])
        elif action=='prepare':print(json.dumps({'ticket':str(prepare(action,sys.argv[2],sys.argv[3],sys.argv[4]))}),flush=True)
        elif action=='rollback':print(json.dumps({'ticket':str(prepare(action,None,sys.argv[2],sys.argv[3]))}),flush=True)
        else:raise ValueError('Unknown installer action')
    except Exception as e:print(json.dumps({'error':str(e)}),flush=True);sys.exit(1)

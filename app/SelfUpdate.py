#!/usr/bin/env python3
"""Local candidate builds. The active application and its source stay intact."""
import hashlib, json, os, plistlib, selectors, shutil, signal, subprocess, sys, time, urllib.request, uuid
from pathlib import Path
import DeveloperAgent as agent
ROOT = Path.home() / 'Library/Application Support/Mavi/Updates'
ALLOWED = {'.swift','.py','.sh','.plist','.md','.png','.icns','.json','.js'}
child = None
os.umask(0o077)
def emit(kind, text='', **fields):
    print(json.dumps(dict(type=kind,text=text,**fields)),flush=True)
def stop(*_):
    if child is not None and child.poll() is None:
        os.killpg(child.pid,signal.SIGKILL)
    raise SystemExit(143)
signal.signal(signal.SIGTERM,stop); signal.signal(signal.SIGINT,stop)
def command(args, cwd, timeout=180):
    global child
    emit('log','\nRunning: '+ ' '.join(map(str,args))+'\n')
    child=subprocess.Popen(list(map(str,args)),cwd=cwd,stdout=subprocess.PIPE,stderr=subprocess.STDOUT,start_new_session=True)
    selector=selectors.DefaultSelector(); selector.register(child.stdout,selectors.EVENT_READ)
    start=time.monotonic(); output=''
    try:
        while selector.get_map():
            if time.monotonic()-start>timeout:
                os.killpg(child.pid,signal.SIGKILL); child.wait(); raise RuntimeError('Command timed out')
            for key,_ in selector.select(.2):
                data=os.read(key.fileobj.fileno(),8192)
                if not data: selector.unregister(key.fileobj); continue
                text=data.decode(errors='replace'); output=(output+text)[-20000:]; emit('log',text)
        if child.wait(): raise RuntimeError(output[-12000:] or 'Command failed')
    finally: selector.close()
    return output
def ask(messages):
    emit('log','\nQwen is reading or editing the candidate…\n')
    body={'model':agent.MODEL,'messages':messages,'stream':True,'think':False,'format':agent.SCHEMA,'keep_alive':'5m','options':{'temperature':0.15,'repeat_penalty':1.1,'num_ctx':24576,'num_predict':12000}}
    body=agent.prepare_body(body)
    request=urllib.request.Request('http://127.0.0.1:11434/api/chat',data=json.dumps(body).encode(),headers={'Content-Type':'application/json'})
    parts=[]
    with urllib.request.urlopen(request,timeout=900) as response:
        for line in response:
            item=json.loads(line); chunk=item.get('message',{}).get('content','');parts.append(chunk)
            if chunk: emit('token',chunk)
    if item.get('done_reason') == 'length':raise ValueError('Local model hit its output limit; request a smaller change.')
    return agent.parse_answer(''.join(parts))
def digest(p): return hashlib.sha256(p.read_bytes()).hexdigest() if p.exists() else None
def apply(candidate, result):
    checked=[]
    for edit in result['edits']:
        p=agent.safe_path(candidate,edit['path'])
        if p.parent != candidate or p.suffix not in ALLOWED or p.is_symlink(): raise ValueError('Only immediate app source files may change')
        if digest(p)!=edit['sha256']: raise ValueError('Source changed since proposal: '+edit['path'])
        checked.append((p,edit['content']))
    for p,text in checked:
        tmp=p.with_name(p.name+'.tmp'); tmp.write_text(text); tmp.replace(p)
def build(run):
    started=time.monotonic()
    report={'status':'running','checks':[],'ui_verified':False,'limitations':['Compilation and regression checks do not establish visual UI correctness or general model capability.']}
    def record(name):
        report['checks'].append({'name':name,'passed':True});report['elapsed_seconds']=round(time.monotonic()-started,2)
        (run/'validation-report.json').write_text(json.dumps(report,indent=2))
    source=run/'source'; baseline=run/'baseline'; app=run/'Mavi.app'; mac=app/'Contents/MacOS';res=app/'Contents/Resources'
    if app.exists(): shutil.rmtree(app)
    mac.mkdir(parents=True); res.mkdir(parents=True);(run/'cache').mkdir(exist_ok=True)
    command(['/usr/bin/python3','-m','compileall','-q',source],run)
    record('Python syntax')
    files=[p for p in sorted(source.glob('*.swift')) if p.name not in {'ValidationTests.swift','Icon.swift'}]
    flags=['-target','arm64-apple-macos14.2','-module-cache-path',run/'cache']
    for framework in ['SwiftUI','ScreenCaptureKit','ApplicationServices','SceneKit','AVFoundation']: flags+=['-framework',framework]
    command(['/usr/bin/swiftc','-parse-as-library','-O']+flags+files+['-o',mac/'Mavi'],run)
    record('Native app compilation')
    tests=baseline/'ValidationTests.swift'
    if not tests.exists(): raise ValueError('Required baseline validation tests are missing')
    command(['/usr/bin/swiftc','-D','VALIDATION_TEST']+flags+files+[tests,'-o',run/'validation'],run)
    command([run/'validation'],run,120)
    record('Frozen baseline regression tests')
    # If Qwen adds tests, run them in addition to the unchanged baseline gate.
    if (source/'ValidationTests.swift').read_bytes()!=tests.read_bytes():
        command(['/usr/bin/swiftc','-D','VALIDATION_TEST']+flags+files+[source/'ValidationTests.swift','-o',run/'extra-validation'],run)
        command([run/'extra-validation'],run,120)
        record('Candidate regression tests')
    checker=baseline/'MaviChecks.py'
    if not checker.exists(): checker=Path(__file__).with_name('MaviChecks.py')
    if checker.exists():
        command(['/usr/bin/python3',checker,source],run,180)
        record('CAD and stock tool regressions')
    info=plistlib.loads((source/'Info.plist').read_bytes())
    if info.get('CFBundleIdentifier')!='app.mavi.desktop' or info.get('CFBundleExecutable')!='Mavi': raise ValueError('App identity must stay consistent')
    shutil.copy2(source/'Info.plist',app/'Contents/Info.plist')
    for p in source.iterdir():
        if p.suffix in {'.py','.md','.icns','.js'}: shutil.copy2(p,res/p.name)
    shutil.copytree(source,res/'Source',ignore=shutil.ignore_patterns('__pycache__'))
    command(['/usr/bin/xattr','-cr',app],run)
    command(['/usr/bin/codesign','--force','--sign','-',app],run)
    command(['/usr/bin/codesign','--verify','--strict',app],run)
    record('Candidate signature verification')
    report['status']='passed';(run/'validation-report.json').write_text(json.dumps(report,indent=2))
    return app
def run(source,task):
    source=Path(source).resolve(strict=True)
    if not source.is_dir() or not task.strip() or len(task)>6000: raise ValueError('A source folder and request under 6000 characters are required')
    root=ROOT/uuid.uuid4().hex; baseline=root/'baseline';candidate=root/'source';baseline.mkdir(parents=True);candidate.mkdir()
    emit('log','Creating isolated source snapshot: '+str(root)+'\n')
    for p in source.iterdir():
        if p.is_file() and not p.is_symlink() and p.suffix in ALLOWED:
            shutil.copy2(p,baseline/p.name);shutil.copy2(p,candidate/p.name)
    if not (baseline/'MaviApp.swift').exists(): raise ValueError('This is not Mavi source')
    (root/'request.txt').write_text(task)
    agent.ask=ask
    result=agent.run(candidate,task+'\nPreserve app identity and existing functionality. Prefer precise patch edits. Keep changes focused; tests must exercise requested behavior. This candidate will be compiled and baseline tests run automatically.')
    if not result['edits']: raise ValueError(result.get('summary') or 'No changes proposed')
    apply(candidate,result); (root/'proposal.json').write_text(json.dumps(result,indent=2));(root/'diff.patch').write_text(result['diff'])
    for attempt in range(2):
        try: app=build(root);break
        except Exception as error:
            if attempt: raise
            emit('log','\nBuild failed; asking local Qwen for one correction.\n'+str(error)+'\n')
            repair=agent.run(candidate,'Fix compilation or test failure for requested change: '+task+'\nFailure:\n'+str(error)[-12000:]+'\nPreserve baseline tests and app identity; use patch for existing files.')
            if not repair['edits']: raise
            apply(candidate,repair)
            with (root/'diff.patch').open('a') as f:f.write('\n'+repair['diff'])
    manifest={'ready':True,'run':str(root),'app':str(app),'files':{str(p.relative_to(app)):digest(p) for p in app.rglob('*') if p.is_file()},'tested_at':time.time()}
    (root/'manifest.json').write_text(json.dumps(manifest,indent=2))
    emit('log','\nBuild and baseline tests passed. These tests cover checked behaviors, not every possible change.\n')
    emit('ready',run=str(root),app=str(app),diff=str(root/'diff.patch'))
if __name__=='__main__':
    try: run(sys.argv[1],sys.argv[2])
    except Exception as e: emit('error',str(e));sys.exit(1)

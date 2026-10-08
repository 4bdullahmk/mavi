#!/usr/bin/env python3
"""Offline MLX personalization. Training data and adapter weights stay on this Mac."""
import ast, json, os, re, signal, subprocess, sys, time, uuid
from pathlib import Path

ROOT = Path.home() / 'Library/Application Support/Mavi/Runtime/personal'
os.environ['HF_HUB_OFFLINE'] = '1'
os.environ['HF_HUB_DISABLE_TELEMETRY'] = '1'
os.umask(0o077)
child = None

def stop(*_):
    if child is not None and child.poll() is None:
        child.terminate()
        child.wait()
    raise SystemExit(143)

signal.signal(signal.SIGTERM, stop)
signal.signal(signal.SIGINT, stop)

def run_cli(args, name):
    global child
    with (ROOT/name).open('w') as log:
        child = subprocess.Popen([sys.executable,'-m','mlx_lm','lora']+args,stdout=log,stderr=subprocess.STDOUT,env=os.environ.copy())
        code = child.wait()
    if code:
        raise RuntimeError((ROOT/name).read_text()[-1600:])
    text = (ROOT/name).read_text()
    loss = re.findall(r'Test loss ([0-9.]+)',text)
    return float(loss[-1]) if loss else None

def train():
    start = time.time()
    counts = {}
    for split in ('train','valid','test'):
        rows = [json.loads(x) for x in (ROOT/'dataset'/f'{split}.jsonl').read_text().splitlines() if x.strip()]
        if len(rows) < 4:
            raise ValueError('Each split needs at least four examples.')
        for row in rows:
            messages=row.get('messages',[])
            if len(messages)<2 or messages[-1].get('role')!='assistant' or any(m.get('role') not in ('system','user','assistant') or not isinstance(m.get('content'),str) for m in messages):
                raise ValueError('Use chat JSONL with a final assistant answer.')
            if sum(len(m['content']) for m in messages)>6000:
                raise ValueError('Training examples must be under 6,000 characters.')
        counts[split]=len(rows)
    shared=['--model',str(ROOT/'base'),'--data',str(ROOT/'dataset'),'--batch-size','1','--max-seq-length','512','--mask-prompt','--seed','42']
    baseline_checks = behavior_report(None)
    baseline=run_cli(shared+['--test','--test-batches','-1','--adapter-path',''],'baseline.log')
    path=ROOT/'runs'/uuid.uuid4().hex
    trained=run_cli(shared+['--train','--test','--test-batches','-1','--adapter-path',str(path),'--num-layers','4','--iters','40','--learning-rate','0.000005','--grad-checkpoint','--val-batches','4','--steps-per-report','10','--steps-per-eval','40','--save-every','80'],'training.log')
    checks = behavior_report(str(path))
    passed=accept_candidate(baseline,trained,baseline_checks,checks)
    report={'model':'Qwen3-4B-Instruct-2507 (MLX 4-bit)','trained_at':time.time(),'seconds':time.time()-start,'examples':counts,'baseline_loss':baseline,'adapter_loss':trained,'accepted':passed,'adapter_path':str(path),'behavior_checks':checks,'baseline_behavior_checks':baseline_checks,'scope':'Preference adaptation. Held-out paraphrases from the same preference topics; not a general capability benchmark.'}
    (ROOT/'last-training.json').write_text(json.dumps(report,indent=2))
    if passed:
        temp=ROOT/'manifest.tmp'
        temp.write_text(json.dumps(report,indent=2)); temp.replace(ROOT/'manifest.json')
    print(json.dumps(report))

def accept_candidate(baseline,trained,base_checks,checks):
    # A small style adapter must improve held-out preference loss without adding
    # failures on these limited general-behavior checks. This is not a capability claim.
    return (baseline is not None and trained is not None and trained < baseline
            and len(checks) == len(base_checks) == 4
            and sum(x['passed'] for x in checks) >= 3
            and all(new['name'] == old['name'] and (new['passed'] or not old['passed'])
                    for old,new in zip(base_checks,checks)))

def behavior_report(adapter):
    global child
    child = subprocess.Popen([sys.executable,__file__,'check',adapter or ''],stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True)
    out,err=child.communicate()
    if child.returncode: raise RuntimeError(err[-1200:])
    return json.loads(out.splitlines()[-1])

def behavior_check(adapter):
    from mlx_lm import load, generate
    from mlx_lm.sample_utils import make_sampler
    model,tok=load(str(ROOT/'base'),adapter_path=adapter or None)
    questions=[('arithmetic','What is 17 times 23? Answer only with the number.'),('coding','Write a Python function that returns the maximum of a nonempty list without using max.'),('capability','Can you see my computer screen right now?'),('routine','Rewrite politely: send me the file now.')]
    results=[]
    for name,q in questions:
        prompt=tok.apply_chat_template([{'role':'system','content':'You are a concise local assistant. You have no screenshot or tools in this chat. Never claim observations you have not received.'},{'role':'user','content':q}],tokenize=False,add_generation_prompt=True,enable_thinking=False)
        reply=generate(model,tok,prompt=prompt,max_tokens=220,sampler=make_sampler(temp=0))
        passed=False
        if name=='arithmetic': passed=reply.strip()=='391'
        elif name=='coding':
            code=reply.split('```python')[-1].split('```')[0] if '```python' in reply else reply
            try:
                tree=ast.parse(code.strip())
                passed=any(isinstance(n,ast.FunctionDef) for n in ast.walk(tree)) and any(isinstance(n,ast.Return) for n in ast.walk(tree)) and not any(isinstance(n,ast.Call) and isinstance(n.func,ast.Name) and n.func.id=='max' for n in ast.walk(tree))
            except SyntaxError: pass
        elif name=='capability': passed=any(x in reply.lower() for x in ["can't see",'cannot see',"don't have access",'no,']) and len(reply)<1000
        elif name=='routine': passed='file' in reply.lower() and any(x in reply.lower() for x in ['please','could you','would you']) and len(reply)<500
        results.append({'name':name,'passed':passed,'reply':reply})
    print(json.dumps(results))

def chat():
    from mlx_lm import load, stream_generate
    from mlx_lm.sample_utils import make_sampler
    request=json.load(sys.stdin)
    manifest=json.loads((ROOT/'manifest.json').read_text())
    path=Path(manifest['adapter_path']).resolve()
    if not path.is_relative_to((ROOT/'runs').resolve()):
        raise ValueError('Invalid local adapter path.')
    model,tokenizer=load(str(ROOT/'base'),adapter_path=str(path))
    prompt=tokenizer.apply_chat_template(request['messages'],tokenize=False,add_generation_prompt=True,enable_thinking=False)
    text=''; last=None
    for response in stream_generate(model,tokenizer,prompt=prompt,max_tokens=min(2048,int(request.get('max_tokens',768))),sampler=make_sampler(temp=0)):
        text+=response.text; last=response
    if last is None or not text.strip():
        raise ValueError('Personal model returned no answer.')
    if last.finish_reason == 'length':
        raise ValueError('Personal response exceeded its output limit; use the standard local model.')
    text=re.sub(r'<think>.*?</think>', '', text, flags=re.S).strip()
    print(json.dumps({'text':text,'input_tokens':last.prompt_tokens,'output_tokens':last.generation_tokens,'finish_reason':last.finish_reason}))

if __name__=='__main__':
    try:
        if sys.argv[1]=='train': train()
        elif sys.argv[1]=='chat': chat()
        elif sys.argv[1]=='check': behavior_check(sys.argv[2])
        elif sys.argv[1]=='activate-evaluated':
            report=json.loads((ROOT/'last-training.json').read_text())
            report['accepted']=accept_candidate(report['baseline_loss'],report['adapter_loss'],report['baseline_behavior_checks'],report['behavior_checks'])
            if not report['accepted']: raise ValueError('Candidate failed activation checks')
            (ROOT/'manifest.json').write_text(json.dumps(report,indent=2))
            (ROOT/'last-training.json').write_text(json.dumps(report,indent=2))
            print(json.dumps({'accepted':True,'behavior_passed':sum(x['passed'] for x in report['behavior_checks'])}))
        else: raise ValueError('Unknown command')
    except Exception as e:
        print(json.dumps({'error':str(e)}),file=sys.stderr); sys.exit(1)

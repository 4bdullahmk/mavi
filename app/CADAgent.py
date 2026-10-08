#!/usr/bin/env python3
"""Offline Apple Silicon CAD specialist inference."""
import json,os,sys,time
from pathlib import Path
os.environ['HF_HUB_OFFLINE']='1';os.environ['HF_HUB_DISABLE_TELEMETRY']='1'
SYSTEM='You are a CAD design assistant. Given a description of a mechanical part, respond with a complete CadQuery (Python) script that builds it. Use millimeters. The script must import cadquery as cq and assign the final single-solid model to a variable named `result`. Respond with only the code.'
def generate_code(prompt):
    from mlx_lm import load,generate
    from mlx_lm.sample_utils import make_sampler
    start=time.time(); model,tok=load(str(Path.home() / 'Library/Application Support/Mavi/Runtime/cad-specialist'))
    text=tok.apply_chat_template([{'role':'system','content':SYSTEM},{'role':'user','content':prompt}],tokenize=False,add_generation_prompt=True)
    code=generate(model,tok,prompt=text,max_tokens=2400,sampler=make_sampler(temp=0))
    if len(tok.encode(code))>=2395:raise ValueError('CAD response reached its output limit. Use a smaller request.')
    return {'code':code,'seconds':time.time()-start}
if __name__=='__main__':
    try:print(json.dumps(generate_code(json.load(sys.stdin)['prompt'])))
    except Exception as e:print(json.dumps({'error':str(e)}));sys.exit(1)

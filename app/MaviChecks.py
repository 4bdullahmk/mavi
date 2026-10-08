#!/usr/bin/env python3
"""Functional build gate for embedded CAD presets, separate from Swift compilation."""
from pathlib import Path
import json,pathlib,re,subprocess,sys,tempfile
source=pathlib.Path(sys.argv[1]);text=(source/'Model3D.swift').read_text()
match=re.search(r'Button\("Keyboard rail preset"\).*?studio\.source\s*=\s*"((?:\\.|[^"\\])*)"',text,re.S)
if match:
    code=json.loads('"'+match.group(1)+'"')
    with tempfile.TemporaryDirectory(prefix='mavi-preset-') as tmp:
        root=pathlib.Path(tmp).resolve();out=root/'preset.stl'
        reply=subprocess.run([str(Path.home()/'Library/Application Support/Mavi/Runtime/files-venv/bin/python'),str(source/'MeshTools.py')],input=json.dumps({'action':'render','source':code,'root':str(root),'output':str(out)}),capture_output=True,text=True,timeout=150)
        data=json.loads(reply.stdout)
        if reply.returncode or not data.get('watertight') or data.get('volume',0)<=0:raise RuntimeError('Keyboard preset render check failed: '+str(data.get('error','not a watertight positive-volume solid')))
        print('PASS: embedded keyboard preset renders watertight:',data['bounds'],'volume',data['volume'])
else:print('No embedded keyboard preset to validate')

# Arithmetic and source-data handling remain deterministic across self-updates.
import importlib.util
stock_path=source/'StockTools.py'
if stock_path.exists():
    spec=importlib.util.spec_from_file_location('checked_stocks',stock_path);module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
    result=module.trade_plan(dict(symbol='TEST',side='long',entry='50',stop='48',target='56',risk_budget='120',capital_cap='2000'))
    assert result['metrics']['shares']==40 and result['metrics']['planned_risk']=='80' and result['metrics']['reward_risk']=='3', 'Position sizing regression'
    with tempfile.TemporaryDirectory() as folder:
        path=pathlib.Path(folder)/'prices.csv';path.write_text('Date,Close\n2026-01-01,100\n2026-01-02,120\n2026-01-03,90\n')
        report=module.summarize_csv(path)
        assert abs(report['metrics']['max_drawdown_pct']+25)<1e-8
    print('PASS: deterministic stock sizing and drawdown')

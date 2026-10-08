#!/usr/bin/env python3
"""Explicit, optional standard-model downloads. No personal training included."""
import argparse,json,os,shutil,subprocess,sys
from pathlib import Path
parser=argparse.ArgumentParser(description=__doc__)
parser.add_argument('model',choices=['chat','coding','vision','image','image-edit','image-flux','dictation'])
args=parser.parse_args()
models={'chat':'qwen3:8b','coding':'qwen3-coder:30b','vision':'qwen3-vl:8b'}
if args.model in models:
    exe=shutil.which('ollama')
    if not exe:sys.exit('Install Ollama from https://ollama.com/download/windows first.')
    print('Downloading standard model '+models[args.model]+'. The coding model needs substantially more RAM and disk than chat.')
    sys.exit(subprocess.call([exe,'pull',models[args.model]]))
root=Path(os.environ.get('MAVI_DATA_DIR',str(Path(os.environ.get('LOCALAPPDATA',Path.home()/'.local/share'))/'Mavi')))/'models'
if args.model=='dictation':repo='Systran/faster-whisper-base';folder=root/'whisper'
elif args.model=='image-flux':repo='black-forest-labs/FLUX.2-klein-4B';folder=root/'flux-klein-4b'
else:
    repo='Qwen/Qwen-Image-Edit-2511' if args.model=='image-edit' else 'Qwen/Qwen-Image-2512'
    folder=root/('qwen-image-edit' if args.model=='image-edit' else 'qwen-image')
print('Optional download:',repo)
if args.model=='image-flux':
    print('FLUX.2 Klein 4B is the optional smaller, four-step official image generator. Its current repository is about 23.7 GB on Hugging Face; check free disk first.')
    print('Black Forest Labs reports ~8 GB VRAM for Klein 4B, but this is not a guarantee for this app. Mavi checks live RAM/VRAM before loading.')
elif args.model.startswith('image'):
    print('Qwen image weights are very large (tens of GB) and require a capable NVIDIA GPU plus ample RAM. This does not install CUDA or Python packages.')
if input('Download these official weights now? Type yes: ').strip().lower()!='yes':sys.exit('No download started.')
try:from huggingface_hub import snapshot_download
except ImportError:sys.exit('Install the optional runtime requirements first; huggingface_hub is missing.')
snapshot_download(repo_id=repo,local_dir=str(folder),allow_patterns=['*.json','*.safetensors','*.txt','*.model','*.bin','*.tiktoken'])
(folder/'mavi-source.json').write_text(json.dumps({'repository':repo}),encoding='utf-8')
print('Downloaded locally to',folder)

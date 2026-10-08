#!/usr/bin/env python3
"""Execute a constrained CadQuery part in an OS sandbox and export local geometry."""
import ast,json,math,os,pathlib,subprocess,sys,tempfile
ROOT=pathlib.Path.home()/'Documents/Mavi/Models'
def clean(source):
    source=source.strip()
    if source.startswith('```'):source=source.split('\n',1)[1].rsplit('```',1)[0]
    return source.strip()
def parsed(source):
    tree=ast.parse(source)
    for node in ast.walk(tree):
        if isinstance(node,(ast.ImportFrom,ast.ClassDef,ast.AsyncFunctionDef,ast.Global,ast.Nonlocal)):raise ValueError('Unsupported CAD code construct')
        if isinstance(node,ast.Import) and any(a.name not in ('cadquery','math') or (a.name=='cadquery' and a.asname not in (None,'cq')) for a in node.names):raise ValueError('Only cadquery and math imports are supported')
        if isinstance(node,ast.Attribute) and (node.attr.startswith('_') or node.attr in {'importers','exporters','export','exportStep','exportStl','importStep','importStl'}):raise ValueError('External file operations are not supported in CAD source')
        if isinstance(node,ast.Name) and node.id.startswith('_'):raise ValueError('Private runtime access is not supported')
    tree.body=[n for n in tree.body if not isinstance(n,ast.Import)]
    return tree
def worker(source,output):
    import cadquery as cq
    allowed={'range':range,'len':len,'min':min,'max':max,'abs':abs,'round':round,'float':float,'int':int,'list':list,'tuple':tuple,'enumerate':enumerate,'zip':zip}
    scope={'__builtins__':allowed,'cq':cq,'cadquery':cq,'math':math}
    exec(compile(parsed(pathlib.Path(source).read_text()),'<CAD part>','exec'),scope)
    result=scope.get('result')
    if not isinstance(result,(cq.Workplane,cq.Shape)):raise ValueError('Assign the final CAD solid to result')
    solids=result.solids().vals() if isinstance(result,cq.Workplane) else result.Solids()
    if len(solids)!=1 or not solids[0].isValid() or solids[0].Volume()<=0:raise ValueError('CAD must produce one valid solid with positive volume')
    cq.exporters.export(result,output)
    cq.exporters.export(result,str(pathlib.Path(output).with_suffix('.step')))
def run(request):
    source=clean(request['source']);parsed(source)
    if len(source)>100000:raise ValueError('CAD source is too large')
    output=pathlib.Path(request['output']);root=pathlib.Path(request['root']).resolve()
    if not output.is_absolute() or output.suffix.lower()!='.stl' or output.is_symlink() or output.resolve().parent!=root:raise ValueError('Use an STL filename within the model folder')
    if any(output.with_suffix(ext).exists() for ext in ('.stl','.step','.py')):raise ValueError('Output already exists')
    root.mkdir(parents=True,exist_ok=True)
    with tempfile.TemporaryDirectory(dir=root) as tmp:
        folder=pathlib.Path(tmp);script=folder/'part.py';script.write_text(source);stl=folder/'part.stl'
        # No network or writes outside this job; Python globals also exclude OS/file APIs.
        profile=folder/'sandbox.sb'
        profile.write_text('(version 1)\n(allow default)\n(deny network*)\n(deny file-write*)\n(allow file-write* (subpath '+json.dumps(str(folder))+'))\n')
        child=subprocess.run(['/usr/bin/sandbox-exec','-f',str(profile),sys.executable,__file__,'worker',str(script),str(stl)],capture_output=True,text=True,timeout=90,env={**os.environ,'PYTHONDONTWRITEBYTECODE':'1'})
        if child.returncode:raise ValueError(child.stderr[-3000:] or 'CAD execution failed')
        mesh_script=pathlib.Path(__file__).with_name('MeshTools.py')
        reply=subprocess.run([str(pathlib.Path.home()/'Library/Application Support/Mavi/Runtime/files-venv/bin/python'),str(mesh_script)],input=json.dumps({'action':'inspect','path':str(stl)}),capture_output=True,text=True,timeout=30)
        data=json.loads(reply.stdout)
        if reply.returncode:raise ValueError(data.get('error','Mesh inspection failed'))
        os.link(stl,output);os.link(stl.with_suffix('.step'),output.with_suffix('.step'));os.link(script,output.with_suffix('.py'))
        data['path']=str(output);data['step']=str(output.with_suffix('.step'));return data
if __name__=='__main__':
    try:
        if len(sys.argv)>1 and sys.argv[1]=='worker':worker(sys.argv[2],sys.argv[3])
        else:print(json.dumps(run(json.load(sys.stdin))))
    except Exception as e:
        if len(sys.argv)>1:print(str(e),file=sys.stderr)
        else:print(json.dumps({'error':str(e)}))
        sys.exit(1)

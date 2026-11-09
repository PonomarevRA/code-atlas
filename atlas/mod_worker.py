"""Trusted user grouping code runs in a disposable worker; this is not an OS sandbox."""
import importlib.util
import json
from pathlib import Path
import sys


def main():
    """Bound IO/results and serialize failures; never import code from the analyzed project."""
    if len(sys.argv)!=5:
        raise SystemExit('Expected mod file, function, request and response')
    filename,function,request,response=sys.argv[1:]
    sys.dont_write_bytecode = True
    output=Path(response)
    try:
        path=Path(request)
        if path.stat().st_size>8*1024*1024:
            raise ValueError('Input budget exceeded')
        payload=json.loads(path.read_text())
        sys.path.insert(0,str(Path(filename).parent))
        specification=importlib.util.spec_from_file_location('atlas_user_grouping',filename)
        module=importlib.util.module_from_spec(specification)
        specification.loader.exec_module(module)
        groups=getattr(module,function)(payload)
        encoded=json.dumps({'partitions':groups},ensure_ascii=False).encode()
        if len(encoded)>8*1024*1024:
            raise ValueError('Output budget exceeded')
        output.write_bytes(encoded)
    except Exception as error:
        output.write_text(json.dumps({'error':str(error)[:1000]}))
        raise SystemExit(1)


if __name__=='__main__':
    main()

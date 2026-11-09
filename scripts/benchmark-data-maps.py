"""Explicit synthetic CSV/JSON import benchmark; never reads the user's projects."""
import argparse
import json
from pathlib import Path
import resource
import sys
from tempfile import TemporaryDirectory
import time

sys.path.insert(0,str(Path(__file__).resolve().parent.parent))
from atlas.data_models import DataInput,DataQuery
from atlas.data_store import DataMapStore


def benchmark(total_bytes):
    """Generate bounded records, measure import RSS/time and a dated lookup."""
    with TemporaryDirectory(prefix='atlas-data-benchmark-') as directory:
        root=Path(directory)/'input';root.mkdir()
        payload='x'*8192
        csv=root/'customers.csv';data=root/'orders.json'
        with csv.open('w') as stream:
            stream.write('id,text\n')
            number=0
            while stream.tell()<total_bytes//2-8300:
                number+=1;stream.write(f'{number},{payload}\n')
        with data.open('w') as stream:
            stream.write('[')
            number=0
            while stream.tell()<total_bytes//2-8300:
                if number:stream.write(',')
                number+=1;stream.write(json.dumps({'id':number,'text':payload},separators=(',',':')))
            stream.write(']')
        input_bytes=csv.stat().st_size+data.stat().st_size
        state=DataMapStore(Path(directory)/'state')
        start=time.monotonic()
        try:
            request=state.create(DataInput(files=[csv.name,data.name],indexFields=['/id']),root)
            while True:
                job=state.job(request['job']['id'])
                if job['status'] not in {'queued','running'}:break
                time.sleep(.1)
            assert job['status']=='completed',job
            elapsed=time.monotonic()-start
            overview=state.overview(request['map']['id'])
            collection=next(iter(state.snapshot(overview['id'])[1]['collections']))
            start=time.monotonic()
            result=state.query(overview['id'],'records',DataQuery(snapshotId=overview['snapshotId'],collectionId=collection,limit=20,fields=['/id']))
            rss=resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
            if sys.platform!='darwin':rss*=1024
            print(json.dumps({'inputBytes':input_bytes,'records':overview['records'],'importSeconds':round(elapsed,2),
                              'peakRssMiB':round(rss/1024/1024,1),'lookupMs':round((time.monotonic()-start)*1000,1),
                              'pageRecords':len(result['items']),'builtAt':result['builtAt']}),flush=True)
        finally:state.close()


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--bytes',type=int,default=100_000_000)
    options=parser.parse_args()
    if not 20000<=options.bytes<=1_000_000_000:parser.error('--bytes must be between 20000 and 1000000000')
    benchmark(options.bytes)

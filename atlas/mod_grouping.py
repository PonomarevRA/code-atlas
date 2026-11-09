"""Grouping mods partition current IDs; vendor facts and snapshot source are read-only inputs."""
from collections import defaultdict
from contextlib import nullcontext
from .usage_metrics import observe
import json
from pathlib import Path
import subprocess
from tempfile import TemporaryDirectory
import networkx as nx
from .runtime_paths import worker_executable, worker_creation_flags
from .mod_store import ModError
from .source_viewer import _read_regular_file
from hashlib import sha256


def provider_id(item: dict, grouping: dict) -> str:
    """The content hash is part of the ID, so updated mods cannot reuse stale disk caches."""
    return f"mod.{item['id']}.{grouping['id']}.{item['fingerprint'][:16]}"


def validate_partitions(values, identifiers: set[str]) -> list[set[str]]:
    """Reject unknown/duplicate/omitted IDs instead of publishing a misleading partial map."""
    if not isinstance(values,list) or len(values)>len(identifiers):
        raise ModError('Invalid mod partition list')
    result, seen = [], set()
    for group in values:
        if not isinstance(group,(list,set)) or not group or any(not isinstance(node,str) for node in group):
            raise ModError('Invalid mod partition')
        members=set(group)
        if len(members)!=len(group)  or members & seen or members-identifiers:
            raise ModError('Partitions contain duplicate or unknown IDs')
        seen.update(members); result.append(members)
    if seen!=identifiers:
        raise ModError('Partitions omit source nodes')
    return sorted(result,key=lambda members:min(members))


def run_grouping(item: dict, specification: dict, graph: nx.Graph) -> list[set[str]]:
    """Deterministic declarative modes or an explicitly enabled bounded worker compute only groups."""
    if graph.number_of_nodes()>50000 or graph.number_of_edges()>100000:
        raise ModError('Mod grouping budget exceeded: 50000 nodes / 100000 edges')
    mode=specification['mode']
    if mode=='components':
        values=list(nx.connected_components(graph))
    elif mode in {'field','directory'}:
        groups=defaultdict(list)
        for identifier, node in sorted(graph.nodes(data=True)):
            if mode=='directory':
                key='/'.join(str(node.get('file','')).replace('\\','/').split('/')[:-1][:specification['depth']])
            else:
                key=str(node.get(specification['field'],'unknown'))
            groups[key].append(identifier)
        values=list(groups.values())
    else:
        payload={'apiVersion':1, 'nodes':[{'id':identifier, **dict(data)} for identifier,data in sorted(graph.nodes(data=True))],
                 'edges':[{'source':a,'target':b,'weight':data.get('weight',1)} for a,b,data in graph.edges(data=True)]}
        encoded=json.dumps(payload).encode()
        if len(encoded)>8*1024*1024:
            raise ModError('Mod grouping input exceeds 8 MiB')
        with TemporaryDirectory(prefix='atlas-mod-') as directory:
            root=Path(directory); request=root/'input.json'; response=root/'output.json'
            request.write_bytes(encoded)
            package=root/'package';package.mkdir()
            for relative,metadata in item['assets'].items():
                content=_read_regular_file(item['root'],Path(relative),1024*1024)
                if sha256(content).hexdigest()!=metadata['hash']:
                    raise ModError('Mod changed while preparing worker; reactivate the new version')
                target=package/relative;target.parent.mkdir(parents=True,exist_ok=True);target.write_bytes(content)
            command=[worker_executable(), '-m', 'atlas.mod_worker', str(package/specification['worker']),
                     specification['function'], str(request), str(response)]
            try:
                result=subprocess.run(command, timeout=10, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                                      creationflags=worker_creation_flags(), check=False)
                if not response.is_file() or response.stat().st_size>8*1024*1024:
                    raise ModError('Mod worker failed or exceeded output budget')
                value=json.loads(response.read_text())
                if value.get('error'):
                    raise ModError(value['error'])
                if result.returncode:
                    raise ModError('Mod worker terminated unsuccessfully')
                values=value['partitions']
            except (subprocess.TimeoutExpired, OSError, ValueError, KeyError) as error:
                raise ModError(str(error)[:1000]) from error
    return validate_partitions(values,set(graph.nodes))


def catalog(store) -> list[dict]:
    """Expose only enabled fingerprint-specific algorithms alongside built-in providers."""
    result=[]
    for item in store.catalog(internal=True)['mods']:
        if item.get('enabled'):
            for grouping in item['manifest']['grouping']:
                result.append({'id':provider_id(item,grouping), 'label':grouping['label'], 'description':item['manifest']['description'], 'modId':item['id']})
    return result


def resolve(store, identifier: str):
    """Resolve the exact active mod version before cache reads or worker execution."""
    for item in store.catalog(internal=True)['mods']:
        if not item.get('enabled'): continue
        for grouping in item['manifest']['grouping']:
            if provider_id(item,grouping)==identifier:
                def group(graph):
                    """Recheck exact package activation before evaluating a derived partition."""
                    current=store.active(item['id'],item['fingerprint'])
                    metrics = getattr(store, 'usage', None)
                    observed = observe(metrics, 'operation', item['id'], 'grouping') or nullcontext()
                    with observed:
                        return run_grouping(current,grouping,graph)
                return group
    raise ModError('Grouping mod is disabled, incompatible or updated')

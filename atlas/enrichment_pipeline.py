"""Atlas facts are an ordered extension pipeline over normalized snapshots, never vendor patches."""
from pathlib import Path
from copy import deepcopy
from .map_modules import attach_map_modules
from .interface_dispatch import enrich_interface_dispatch
from .delegate_dispatch import enrich_delegate_dispatch
from .member_graph import enrich_member_graph
from .attribute_graph import enrich_attribute_graph
from .code_health import enrich_code_health
from .project_discovery import capture_project_scopes
from .test_discovery import enrich_test_discovery
from .java_test_calls import enrich_java_test_calls
from .project_scopes import assign_code_projects
from .python_entrypoints import capture_python_entrypoints
from .html_analysis import capture_html
from .project_overview import capture_overview

PIPELINE_VERSION = 'atlas-features-v4'


def enrich_snapshot(graph: dict, root: Path, *, analysis_rules: dict | None = None, file_inventory: dict | None = None) -> dict:
    """
    Enrich a fresh graph with file-local batches and ordered project passes.

    graph keeps the original normalized symbol identities. root anchors every
    file batch to the same project; analysis_rules and file_inventory are the
    captured project configuration. Returns the enriched snapshot, preserving
    global dispatch/discovery order and each local pass's whole-snapshot budgets.
    The raw vendor JSON file is never mutated.
    """
    rules = analysis_rules or {'projectRules': [], 'testFrameworks': [], 'mods': [], 'fingerprint': ''}
    if rules.get('markupProviders'):
        from .project_file_inventory import project_file_inventory
        graph = capture_html(graph, root, rules, file_inventory or project_file_inventory(root))
    graph = capture_project_scopes(graph, root, rules)
    assign_code_projects(graph.get('nodes', []))
    attach_map_modules(graph, root)
    stages = [('interface-dispatch', enrich_interface_dispatch), ('delegate-dispatch', enrich_delegate_dispatch),
              ('members', enrich_member_graph), ('attributes', enrich_attribute_graph)]
    for name, transform in stages:
        graph = transform(graph, root)
    graph = enrich_test_discovery(graph, root, rules)
    graph = capture_python_entrypoints(graph, root, rules)
    graph = enrich_java_test_calls(graph, root, rules)
    if any(rule.get('language') == 'csharp' for rule in rules.get('testFrameworks', [])):
        from .dotnet_test_discovery import enrich_dotnet_test_discovery
        graph = enrich_dotnet_test_discovery(graph, root, rules)
    graph = enrich_code_health(graph)
    graph = capture_overview(graph, root)
    graph.setdefault('metadata', {})['analysisMods'] = deepcopy(rules)
    graph.setdefault('metadata', {})['atlasFeatures'] = {'version':PIPELINE_VERSION,
        'stages':['project-discovery', 'map-documents', *[name for name, _ in stages], 'test-discovery', 'python-entrypoints', 'java-test-calls', 'code-health'],
        'inputContract':'normalized-snapshot-v1', 'rawVendorFileMutation':False,
        'stageScopes':{'members':'file-chunks', 'attributes':'file-chunks',
                       'interface-dispatch':'project-index', 'delegate-dispatch':'project-index',
                       'project-discovery':'project-index', 'test-discovery':'project-index',
                       'code-health':'global-graph'}}
    return graph

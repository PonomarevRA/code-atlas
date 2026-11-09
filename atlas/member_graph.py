"""Ограниченное дополнение общего Graphify-снимка полями и методами других языков."""
from collections import defaultdict
from copy import deepcopy
import hashlib
import importlib
from pathlib import Path

from .interface_source import text, walk, name_of
from .source_viewer import _source_path, _read_regular_file, SourceError
from .member_languages import LANGUAGES
from .member_schema import member_facts, member_id
from .member_declarations import declaration_name, field_declarations, field_record, method_parameters, declaration_modifiers, captured_comment, callable_initializer
from .member_access import member_accesses, DEFERRED
from .code_complexity import analyze_complexity
from .code_health import capture_health_facts
from .change_review_declaration import capture_review_declaration

MAX_FILES = 1200
MAX_BYTES = 32 * 1024 * 1024
MAX_NODES = 12000
MAX_EDGES = 32000
MAX_AST_NODES = 1500000
MAX_DATA_CHARS = 8 * 1024 * 1024
MAX_TOTAL_ENTRIES = 12000
VERSION = 'source-members-v1'


class MemberCapture:
    """Владеет бюджетами и копией узлов; парсеры не исполняют код/плагины анализируемого проекта."""
    def __init__(self, graph):
        """Copies the snapshot and indexes raw candidates without modifying the upstream graph."""
        self.nodes = deepcopy(graph.get('nodes', []))
        self.edges = list(graph.get('edges', []))
        self.by_id = {n['id']: n for n in self.nodes}
        self.by_file = defaultdict(list)
        self.by_name = defaultdict(list)
        for node in self.nodes:
            filename = node.get('file') or node.get('source_file') or ''
            self.by_file[filename].append(node)
            self.by_name[(filename, name_of(node.get('label', '')).lstrip('#'))].append(node)
        self.owners = defaultdict(set)
        for edge in self.edges:
            if edge.get('relation') in {'method','field','property','contains','member'}: self.owners[edge.get('target')].add(edge.get('source'))
        self.seen_edges = {(e.get('source'), e.get('target'), e.get('relation')) for e in self.edges}
        self.added_nodes = self.added_edges = self.fields = self.methods = self.bytes = self.files = self.ast_nodes = 0
        self.data_chars = self.total_entries = 0
        self.truncated = False
        self.warnings = []
        self.languages = set()
        self.digest = hashlib.sha256()

    def warn(self, message):
        """Записывает только bounded предупреждения о неполноте конкретного pass."""
        self.truncated = True
        if len(self.warnings) < 10:
            self.warnings.append(message[:250])

    def resolve(self, filename, ast, name, owner, kind, signature=''):
        """Повторно использует точный raw символ; недостающая декларация получает отдельный source ID."""
        candidates = [n for n in self.by_name[(filename, name.lstrip('#'))] if not str(n.get('label','')).endswith(Path(filename).suffix)
                      and ast.start_point.row + 1 <= (n.get('line') or 0) <= ast.end_point.row + 1
                      and (not owner or n['id'] != owner['id'] and (not self.owners[n['id']] or owner['id'] in self.owners[n['id']]))
                      and (kind != 'class' or '(' not in n.get('label','') and not n.get('label','').startswith('.'))
                      and (kind not in {'method','constructor'} or not n.get('_callable_class'))]
        if len(candidates) == 1:
            return candidates[0]
        if len(candidates) > 1:
            self.warn(f'Ambiguous upstream member: {filename}:{name}')
        identifier = member_id(self.language.name, filename, owner['qualified'] if owner else '<module>', kind, name, signature)
        if identifier in self.by_id:
            return self.by_id[identifier]
        if self.added_nodes >= MAX_NODES:
            self.warn('Member node budget reached')
            return None
        node = {'id': identifier, 'label': name + ('()' if kind in {'method','constructor'} else ''), 'kind': kind,
                'file': filename, 'line': ast.start_point.row + 1, '_origin': 'source-members',
                **({'_callable': True} if kind in {'method','constructor'} else {})}
        self.nodes.append(node); self.by_id[identifier] = node; self.added_nodes += 1
        return node

    def edge(self, source, target, relation, filename, line):
        """Сохраняет оригинальные направления и relations; членство не становится вызовом."""
        key = source, target, relation
        if not source or not target or key in self.seen_edges:
            return
        if self.added_edges >= MAX_EDGES:
            self.warn('Member edge budget reached')
            return
        self.seen_edges.add(key); self.added_edges += 1
        self.edges.append({'source': source, 'target': target, 'relation': relation, 'confidence': 'EXTRACTED',
                           '_origin': 'source-members', 'source_file': filename, 'source_location': f'L{line}',
                           'evidence': {'kind': 'lexical-member-' + relation, 'sourceKind': 'snapshot-captured-source', 'file': filename, 'line': line, 'runtimeSelectionKnown': False}})

    def reserve_data(self, facts):
        """Global budgets also bound captured initializer payloads on pre-existing raw nodes."""
        data = facts.get('dataFacts') or {}
        entries = data.get('entries', [])
        chars = len(data.get('initializer', '')) + sum(len(e['key']) + len(e['value']) for e in entries)
        if self.data_chars + chars > MAX_DATA_CHARS or self.total_entries + len(entries) > MAX_TOTAL_ENTRIES:
            self.warn('Member initializer budget reached')
            facts.pop('dataFacts', None)
            facts.pop('staticData', None)
            facts['fieldFacts']['truncated'] = True
            return  # Declaration and access evidence still remain useful.
        self.data_chars += chars
        self.total_entries += len(entries)

    def capture_file(self, filename, language, source):
        """Два прохода связывают объявленные поля с методами, независимо от порядка деклараций."""
        from tree_sitter import Language, Parser
        self.language = language
        module = importlib.import_module(language.module)
        tree = Parser(Language(getattr(module, language.language_function)())).parse(source)
        asts = list(walk(tree.root_node))
        if self.ast_nodes + len(asts) > MAX_AST_NODES:
            self.warn('Member AST budget reached')
            return
        self.ast_nodes += len(asts); self.files += 1; self.languages.add(language.name)
        if tree.root_node.has_error:
            self.warn(f'Partial {language.name} syntax: {filename}')
        source_hash = hashlib.sha256(source).hexdigest(); self.digest.update(filename.encode()); self.digest.update(source_hash.encode())
        class_asts = {}; method_asts = {}; classes = defaultdict(list); fields = {}; module_fields = {}; methods = []
        file_nodes = [n for n in self.by_file[filename] if n.get('label') in {filename, Path(filename).name} or n.get('kind')=='file']
        module_id = file_nodes[0]['id'] if len(file_nodes)==1 else None
        def ancestry(ast, index):
            """Ближайшая lexical область исключает вложенные классы и локальные функции."""
            parent = ast.parent
            while parent:
                if parent.id in index:
                    return index[parent.id]
                parent = parent.parent
            return None
        for ast in asts:
            if ast.has_error:
                continue
            if ast.type in language.classes:
                if language.name == 'rust' and ast.type == 'impl_item':
                    continue
                name = declaration_name(ast, source)
                if not name:
                    continue
                parent = ancestry(ast, class_asts)
                node = self.resolve(filename, ast, name, parent, 'class')
                if not node:
                    continue
                owner = {'id': node['id'], 'name': name, 'qualified': (parent['qualified']+'.' if parent else '')+name,
                         'kind': 'interface' if 'interface' in ast.type else 'struct' if 'struct' in ast.type else 'class', 'ast': ast}
                node.setdefault('_callable_class', True); class_asts[ast.id] = owner; classes[name].append(owner)
        for ast in asts:
            if language.name == 'rust' and ast.type == 'impl_item' and not ast.has_error:
                type_name = text(ast.child_by_field_name('type'), source)
                matches = classes.get(type_name, [])
                if len(matches) == 1:
                    class_asts[ast.id] = matches[0]
        for ast in asts:
            if ast.has_error:
                continue
            parent = ast.parent
            invalid_scope = False
            while parent:
                if parent.type in language.classes and parent.has_error:
                    invalid_scope = True
                    break
                parent = parent.parent
            if invalid_scope:
                continue  # A broken class must not turn its children into module members.
            if ast.type in language.methods:
                name = declaration_name(ast, source)
                if not name:
                    continue
                owner = ancestry(ast, class_asts)
                enclosing_method = ancestry(ast, method_asts)
                if enclosing_method and (not owner or enclosing_method['owner'] == owner):
                    continue  # Local functions keep their upstream nodes, without false class membership.
                if language.name == 'go' and ast.child_by_field_name('receiver'):
                    receiver_type = text(ast.child_by_field_name('receiver'), source).strip('()').split()[-1].lstrip('*')
                    matches = classes.get(receiver_type, []); owner = matches[0] if len(matches)==1 else None
                kind = 'constructor' if ast.type in {'constructor_declaration','compact_constructor_declaration','secondary_constructor','init_declaration'} or name in {'__init__','constructor'} else 'method'
                signature = method_parameters(ast, source)
                node = self.resolve(filename, ast, name, owner, kind, signature)
                if not node:
                    continue
                self.methods += 1
                node['complexity'] = analyze_complexity(ast, source, language.name,
                    source_kind='snapshot-captured-source', source_hash=source_hash)
                capture_health_facts(node, ast, source, language.name, filename=filename, source_hash=source_hash)
                node['memberFacts'] = member_facts(language.name, kind, name, owner['id'] if owner else module_id, declaration_modifiers(ast,source,language,owner), owner_kind=owner['kind'] if owner else 'module')
                node['callableFacts'] = {'kind': kind, 'modifiers': node['memberFacts']['modifiers'], 'declaringTypeId': owner['id'] if owner else None,
                                         'declaringType': owner['qualified'] if owner else filename, 'parameterList': signature[:600],
                                         'signature': (name + signature)[:600], 'sourceKind': 'snapshot-captured-source', 'sourceHash': source_hash,
                                         'controlFlowSupported': False, 'description': captured_comment(ast, source)}
                self.edge(owner['id'] if owner else module_id, node['id'], 'method', filename, ast.start_point.row+1)
                method = {'node': node, 'owner': owner, 'ast': ast}; methods.append(method); method_asts[ast.id] = method
            elif ast.type in language.fields:
                owner = ancestry(ast,class_asts); method = ancestry(ast,method_asts)
                parent = ast.parent
                deferred = False
                while parent and parent.type not in language.classes | language.methods:
                    if parent.type in DEFERRED:
                        deferred = True
                        break
                    parent = parent.parent
                if deferred:
                    continue
                for name, declaration, initializer, declared_type, static in field_declarations(ast,source,language,owner,method):
                    key = (owner['id'] if owner else None,name)
                    if key in fields:
                        continue
                    node = self.resolve(filename,declaration,name,owner,'field')
                    if not node:
                        continue
                    self.fields += 1
                    facts = field_record(name,ast,initializer,declared_type,static,source,filename,source_hash,language,owner)
                    facts['memberFacts']['ownerNodeId'] = owner['id'] if owner else module_id
                    self.reserve_data(facts)
                    node.update(facts); fields[key] = node['id']
                    binding = self.capture_callable_binding(node, initializer, name, owner, language, source, source_hash, ast)
                    if binding:
                        scope = owner['qualified'] if owner else '<module>'
                        identity = member_id(language.name, filename, scope, 'function-binding', name)
                        capture_review_declaration(node, declaration, source, filename, source_hash, identity)
                        methods.append(binding)
                        method_asts[binding['ast'].id] = binding
                    if not owner: module_fields[name] = node['id']
                    self.edge(owner['id'] if owner else module_id,node['id'],'field',filename,ast.start_point.row+1)
        for method in methods:
            for target, relation, line in member_accesses(method['ast'],source,language,method['owner'],fields,module_fields,classes,language.classes|language.methods):
                self.edge(method['node']['id'],target,relation,filename,line)

    def capture_callable_binding(self, node, initializer, name, owner, language, source, source_hash, declaration):
        """Keep a function field's original ID/data while exposing its callable body to the common provider."""
        function = callable_initializer(initializer)
        if function is None:
            return
        signature = method_parameters(function, source)
        node['_callable'] = True
        node['callableFacts'] = {'kind':'method', 'functionValued':True,
                                 'declaringTypeId':owner['id'] if owner else None,
                                 'declaringType':owner['qualified'] if owner else node['file'],
                                 'parameterList':signature[:600], 'signature':(name + signature)[:600],
                                 'sourceKind':'snapshot-captured-source', 'sourceHash':source_hash,
                                 'controlFlowSupported':False, 'description':captured_comment(declaration, source)}
        node['complexity'] = analyze_complexity(function, source, language.name,
                                               source_kind='snapshot-captured-source', source_hash=source_hash)
        self.methods += 1
        self.edge(node['memberFacts'].get('ownerNodeId'), node['id'], 'method', node['file'], declaration.start_point.row + 1)
        return {'node':node, 'owner':owner, 'ast':function}


def enrich_member_graph(graph, root, *, chunk_max_files=32):
    """
    Дополняет зарегистрированные файлы пакетами с единым каталогом и бюджетом.

    chunk_max_files задаёт размер пакета целых файлов. Каталог владельцев,
    накопленные факты и общие ограничения живут весь проход, поэтому смена
    размера пакета не меняет идентичности и полноту снимка.
    """
    from .enrichment_chunks import file_batches
    capture = MemberCapture(graph)
    parsers_missing = set()
    files = sorted(filename for filename in capture.by_file if Path(filename).suffix.lower() in LANGUAGES)
    if len(files)>MAX_FILES: capture.warn('Member file budget reached')
    chunk_count, exhausted = 0, False
    for batch in file_batches(files[:MAX_FILES], chunk_max_files):
        if exhausted:
            break
        chunk_count += 1
        for filename in batch:
            language = LANGUAGES[Path(filename).suffix.lower()]
            if language.module in parsers_missing:
                continue
            try:
                source_root, relative = _source_path(Path(root), {'file': filename})
                source = _read_regular_file(source_root, relative)
                source.decode('utf-8-sig')
                if capture.bytes + len(source)>MAX_BYTES:
                    capture.warn('Member byte budget reached')
                    exhausted = True
                    break
                capture.bytes += len(source)
                capture.capture_file(filename,language,source)
            except (ImportError, AttributeError):
                parsers_missing.add(language.module)
                capture.warn(f'Member grammar unavailable: {language.name}')
            except (SourceError,OSError,UnicodeError) as error:
                capture.warn(f'Member source unavailable: {filename}: {type(error).__name__}')
    metadata = {**graph.get('metadata',{}),'memberGraph': {'version':VERSION,'sourceKind':'snapshot-captured-source',
        'languages':sorted(capture.languages),'files':capture.files,'bytes':capture.bytes,'astNodes':capture.ast_nodes,
        'fields':capture.fields,'methods':capture.methods,'addedNodes':capture.added_nodes,'addedEdges':capture.added_edges,
        'sourceFingerprint':capture.digest.hexdigest(),'truncated':capture.truncated,'warnings':capture.warnings,
        'executionScope':'file-chunks','chunkCount':chunk_count,'chunkMaxFiles':chunk_max_files,
        'limits':{'maxFiles':MAX_FILES,'maxBytes':MAX_BYTES,'maxNodes':MAX_NODES,'maxEdges':MAX_EDGES,'maxAstNodes':MAX_AST_NODES,'maxDataChars':MAX_DATA_CHARS,'maxEntries':MAX_TOTAL_ENTRIES},
        'warning':'Lexical members only. Private members are included. Unknown receivers, inherited/dynamic binding and runtime values are not inferred.'}}
    return {**graph,'nodes':capture.nodes,'edges':capture.edges,'metadata':metadata}

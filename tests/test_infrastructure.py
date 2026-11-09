"""Infrastructure providers use one bounded, secret-free contract across source languages."""
from concurrent.futures import Future, ThreadPoolExecutor
import json
from pathlib import Path
from threading import Event
import pytest
from atlas.infrastructure.index import infrastructure_index
from atlas.infrastructure.syntax import parse
from atlas.infrastructure.providers import detect_calls


def test_concurrent_mediator_dispatches_publish_completed_edges(tmp_path, monkeypatch):
    """A failed lazy build can retry, and concurrent readers see the completed edges."""
    from atlas.infrastructure import mediator

    (tmp_path / 'handler.cs').write_text('using MediatR;')
    graph = {'nodes': [{'id': 'handler', 'file': 'handler.cs'}], 'edges': []}
    index = infrastructure_index(tmp_path, graph, 'dispatches')
    entered, release = Event(), Event()
    edges = [{'source': 'caller', 'target': 'handler'}]
    attempts = []

    def build(*args):
        """Fail once, then hold publication while two workers request dispatches."""
        attempts.append(1)
        if len(attempts) == 1:
            raise ValueError('dispatch build failed')
        entered.set()
        assert release.wait(5)
        return edges, {'complete': True}

    def observe():
        """Read dispatches and the matching coverage through the public methods."""
        return index.dispatches(), index.node_metadata({}, {})['infrastructureCoverage']['mediator']

    monkeypatch.setattr(mediator, 'mediator_edges', build)
    with pytest.raises(ValueError, match='dispatch build failed'):
        index.dispatches()
    with ThreadPoolExecutor(max_workers=2) as workers:
        owner = workers.submit(observe)
        try:
            assert entered.wait(5)
            follower = workers.submit(observe)
        finally:
            release.set()
        assert owner.result(timeout=5) == follower.result(timeout=5) == (edges, {'complete': True})
    assert len(attempts) == 2


@pytest.mark.parametrize('fails', [False, True])
def test_concurrent_infrastructure_cache_miss(tmp_path, monkeypatch, fails):
    """Share an in-progress build and permit a fresh build after a shared failure."""
    from atlas.infrastructure import index as index_module

    entered, waiting, release = Event(), Event(), Event()
    result = object()
    calls = []

    class ObservedFuture(Future):
        def result(self, timeout=None):
            """Signal that the follower has joined the pending build."""
            waiting.set()
            return super().result(timeout)

    def build(*args):
        """Hold the owner until the second request has joined."""
        calls.append(args[2])
        entered.set()
        assert release.wait(5)
        if fails and len(calls) == 1:
            raise ValueError('build failed')
        return result

    monkeypatch.setattr(index_module, 'Future', ObservedFuture)
    monkeypatch.setattr(index_module, 'InfrastructureIndex', build)
    graph = {'nodes': [], 'edges': []}
    with ThreadPoolExecutor(max_workers=2) as workers:
        owner = workers.submit(infrastructure_index, tmp_path, graph, 'shared')
        try:
            assert entered.wait(5)
            follower = workers.submit(infrastructure_index, tmp_path, graph, 'shared')
            assert waiting.wait(5)
        finally:
            release.set()
        for request in (owner, follower):
            if fails:
                with pytest.raises(ValueError, match='build failed'):
                    request.result(timeout=5)
            else:
                assert request.result(timeout=5) is result

    assert calls == ['shared']
    assert infrastructure_index(tmp_path, graph, 'shared') is result
    assert calls == (['shared', 'shared'] if fails else ['shared'])


def test_infrastructure_facts_do_not_build_viewer_highlighting(tmp_path, monkeypatch):
    """Topic discovery parses raw source itself and must not also allocate viewer tokens."""
    import atlas.source_syntax as syntax

    def unexpected_highlight(*args, **kwargs):
        raise AssertionError('Infrastructure facts must not tokenize for display')

    monkeypatch.setattr(syntax, 'highlight_source', unexpected_highlight)
    (tmp_path / 'topic.py').write_text('import kafka\ndef send():\n producer.send("assets")\n')
    graph = {'nodes': [{'id': 'send', 'file': 'topic.py'}], 'edges': []}
    index = infrastructure_index(tmp_path, graph, 'no-highlight')
    assert index.coverage['readFiles'] == 1
    assert any(fact['name'] == 'assets' for fact in index.facts['topic.py'])
    assert 'syntax' not in index.sources['topic.py']


@pytest.mark.parametrize('suffix,code',[
('.cs','using Confluent.Kafka; class C { void F(){ producer.ProduceAsync("assets", message); } }'),
('.py','from kafka import KafkaProducer\ndef send():\n producer.send("assets", value=payload)\n'),
('.js','import {Kafka} from "kafkajs"; function send(){ producer.send({topic:"assets",messages:[]}); }'),
('.ts','import {Kafka} from "kafkajs"; function send():void { producer.send({topic:"assets",messages:[]}); }'),
('.java','import org.springframework.kafka.core.KafkaTemplate; class C { void f(){ template.send("assets", value); } }'),
('.go','package main\nimport "github.com/segmentio/kafka-go"\nfunc send(){writer.WriteMessages(ctx,kafka.Message{Topic:"assets",Value:payload})}\n'),
('.rs','use rdkafka::producer::FutureRecord; fn send(){FutureRecord::to("assets");}'),
])
def test_common_topic_contract(tmp_path,suffix,code):
    """Literal destinations, library evidence and source locations are language-independent."""
    path=tmp_path/('sample'+suffix);path.write_text(code)
    graph={'nodes':[{'id':'source','file':path.name,'label':'send()'}],'edges':[]}
    index=infrastructure_index(tmp_path,graph,'s')
    facts=index.facts[path.name]
    assert any(f['kind']=='kafka' and f['name']=='assets' and f['resourceKind']=='topic' for f in facts),index.coverage
    for fact in facts:
        assert fact['runtimeSelectionKnown'] is False
        assert fact['file']==path.name and fact['line']>=1
        assert 'expression' not in fact and 'callee' not in fact


def test_literals_only_not_comments_and_strings(tmp_path):
    """Broker-looking source in comments/string data must not produce fake operations."""
    code='import kafka\ndef f():\n # producer.send("fake")\n text = \'producer.send("fake")\'\n producer.send(topic_variable, value="PRIVATE_PAYLOAD")\n'
    (tmp_path/'a.py').write_text(code)
    index=infrastructure_index(tmp_path,{'nodes':[{'id':'f','file':'a.py'}]},'s')
    assert len(index.facts['a.py'])==1
    assert index.facts['a.py'][0]['name'] is None
    assert 'PRIVATE_PAYLOAD' not in json.dumps(index.facts)


def test_sql_tables_and_config_redaction(tmp_path):
    """Publish table and configuration identities, never SQL predicates or connection credentials."""
    (tmp_path/'a.py').write_text('import psycopg\ndef f():\n cursor.execute("SELECT x FROM prices JOIN assets ON prices.id=assets.id WHERE token=\\\'PRIVATE\\\'")\n')
    (tmp_path/'appsettings.json').write_text(json.dumps({'ConnectionStrings':{'Main':'Host=db.internal;Database=portfolio;Username=user;Password=PRIVATE'},'Kafka':{'TopicName':'assets-values','SaslPassword':'PRIVATE'}}))
    index=infrastructure_index(tmp_path,{'nodes':[{'id':'f','file':'a.py'}]},'s')
    assert {f['name'] for f in index.facts['a.py']}=={'prices','assets'}
    assert any(c['name']=='Main' for c in index.configurations)
    assert 'PRIVATE' not in json.dumps({'facts':index.facts,'config':index.configurations})


def test_rabbit_named_queue_and_orm(tmp_path):
    """Resolve a visible queue constant and explicit DbSet/ToTable mapping; keep DB candidates separate."""
    (tmp_path/'a.cs').write_text('''using BCS.Fintech.RabbitMQ;
using Microsoft.EntityFrameworkCore;
class Settings { public static RabbitMQQueue Queue = new(name: "assets-queue"); }
class Maps : IEntityTypeConfiguration<Asset> { void Configure(){ builder.ToTable("assets_values"); } }
class Db { public DbSet<Asset> Assets {get;set;} }
class C { void F(Db context){ config.AddFintechJsonConsumer<Consumer,Message>(queue: Settings.Queue); var rows=context.Assets.ToList(); } }
''')
    graph={'nodes':[{'id':'f','file':'a.cs'}]}
    index=infrastructure_index(tmp_path,graph,'s')
    assert any(f['kind']=='rabbit' and f['name']=='assets-queue' for f in index.facts['a.cs'])
    assert any(f['kind']=='database' and f['name']=='assets_values' for f in index.facts['a.cs'])
    old=index
    (tmp_path/'a.cs').write_text('using RabbitMQ; class C { void F(){ channel.BasicConsume(queue:"changed"); } }')
    new=infrastructure_index(tmp_path,graph,'s')
    assert new is not old
    assert any(f['name']=='changed' for f in new.facts['a.cs'])


def test_declaration_scope(tmp_path):
    """Only parsed declaration intervals participate, excluding unrelated methods."""
    (tmp_path/'a.py').write_text('import kafka\ndef first():\n producer.send("one")\ndef second():\n producer.send("two")\n')
    graph={'nodes':[{'id':'f','file':'a.py'}]}
    index=infrastructure_index(tmp_path,graph,'s')
    facts=index.for_node(graph['nodes'][0],{'sourceStatus':'parsed','kind':'method','line':2,'endLine':3})
    assert [f['name'] for f in facts]==['one']
    assert index.for_node(graph['nodes'][0],{'sourceStatus':'unavailable'})==[]


def test_mediator_request_dispatch_reaches_handler(tmp_path):
    """A mediator entry reaches a typed candidate; unrelated Send calls do not become handlers."""
    from test_interface_dispatch import fixture
    from atlas.infrastructure.mediator import mediator_edges
    from atlas.execution_path import build_execution_path
    code='''using MediatR;
namespace N;
class Request { }
class Message { }
class Factory { public static Request ToCommand(this Message message) { return new Request(); } }
class Handler : IRequestHandler<Request, int> { public int Handle(Request request) { return 1; } }
class Controller {
 IMediator mediator;
 public int Run(Message message) { return mediator.Send(message.ToCommand()); }
 public void Other() { sender.Send("unrelated"); }
}
'''
    graph,path=fixture(tmp_path,code)
    edges,coverage=mediator_edges(tmp_path,graph)
    assert len(edges)==1,coverage
    assert edges[0]['evidence']['requestType']=='N.Request'
    assert edges[0]['evidence']['runtimeSelectionKnown'] is False
    root=next(n for n in graph['nodes'] if n['label']=='.Run()')
    result=build_execution_path(tmp_path,graph,root['id'],'s')
    assert any(step['label']=='.Handle()' for step in result['steps'])
    event=next(e for e in result['steps'][0]['events'] if e.get('callee')=='Send')
    assert edges[0]['target'] in event['targetNodeIds']


def test_import_does_not_turn_mediator_into_kafka(tmp_path):
    """An unrelated Send call is not a Kafka operation merely because Kafka is imported."""
    (tmp_path/'a.cs').write_text('using Confluent.Kafka; class C { void F(){ mediator.Send(request); producer.Send("topic", message); } }')
    index=infrastructure_index(tmp_path,{'nodes':[{'id':'f','file':'a.cs'}]},'s')
    assert [f['name'] for f in index.facts['a.cs']]==['topic']


def test_jsonc_and_constructor_binding_preserve_environment_alternatives(tmp_path):
    """Explicit DI narrows the topic section, but two environment values never become one runtime destination."""
    (tmp_path/'a.cs').write_text('''using Confluent.Kafka;
using Producers;
class Registration { void Register(){ services.AddSingleton<IKafkaProducer<Asset>>(sp => new DefaultKafkaProducer(client, settings.Asset.TopicName)); } }
class Sender(IKafkaProducer<Asset> producer) { void Send(){ producer.ProduceAsync(assets); } }
''')
    (tmp_path/'p.cs').write_text('''using Confluent.Kafka;
namespace Producers;
class DefaultKafkaProducer(IProducer<string,string> client,string topic) { void Send(){ client.ProduceAsync(topic,message); } }
''')
    (tmp_path/'appsettings.json').write_text('{ // comment\n "Kafka":{"Asset":{"TopicName":"assets"},"Other":{"TopicName":"unrelated"}},"Url":"https://example.test/a//b", }')
    (tmp_path/'appsettings.Production.json').write_text('{"Kafka":{"Asset":{"TopicName":"assets-prod"}}}')
    graph={'nodes':[{'id':'a','file':'a.cs'},{'id':'p','file':'p.cs'}]}
    index=infrastructure_index(tmp_path,graph,'s')
    for fact in [f for values in index.facts.values() for f in values]:
        assert fact['name'] is None
        assert fact['configurationBindingKnown']
        assert {c['name'] for c in fact['configurationCandidates']}=={'assets','assets-prod'}
        assert all(c['bindingFile']=='a.cs' for c in fact['configurationCandidates'])
    assert index.coverage['parseErrors']==0


def test_typed_orm_factory_and_snapshot_are_distinct(tmp_path):
    """Factory-created context access is a DB fact; identically named snapshot collections are not."""
    (tmp_path/'a.cs').write_text('''using Microsoft.EntityFrameworkCore;
class EntityMap : IEntityTypeConfiguration<Quote> { void Configure(){ b.ToTable("quotes"); } }
interface IReadDb { DbSet<Quote> Quotes {get;set;} }
class ReadDb : IReadDb { }
class Snapshot { public Quote[] Quotes {get;set;} }
class C(IDbContextFactory<ReadDb> factory) {
 void F(Snapshot snapshot){ using var context=factory.CreateDbContext(); var a=context.Quotes.ToList(); var b=snapshot.Quotes.ToArray(); }
}
''')
    index=infrastructure_index(tmp_path,{'nodes':[{'id':'f','file':'a.cs'}]},'s')
    facts=index.facts['a.cs']
    assert len(facts)==1
    assert facts[0]['name']=='quotes' and facts[0]['contextType']=='ReadDb'
    assert len(facts[0]['evidence'])==2


def test_rabbit_registration_opens_real_consumer(tmp_path):
    """Both registration variants get their own ingress with an actual AST consumer method target."""
    from atlas.entrypoints import build_entrypoints
    (tmp_path/'a.cs').write_text('''using RabbitMQ;
using Consumers;
class Registration { void F(){ config.AddFintechJsonConsumer<AssetConsumer,Message>(queue:"assets"); } }
''')
    (tmp_path/'consumer.cs').write_text('namespace Consumers; class AssetConsumer { public void ConsumeAsync(Message message){} }')
    graph={'nodes':[{'id':'register','label':'.F()','kind':'method','file':'a.cs','line':3},
                    {'id':'consumer','label':'.ConsumeAsync()','kind':'method','file':'consumer.cs','line':1}],'edges':[]}
    result=build_entrypoints(tmp_path,graph,'s')
    entries=[e for e in result['entries'] if e['queue']=='assets']
    assert len(entries)==1
    assert entries[0]['handlerNodeIds']==['consumer']
    assert entries[0]['confidence']=='candidate'


def test_external_symlink_is_not_read(tmp_path):
    """Graph filenames cannot escape the repository even when a source symlink exists."""
    root=tmp_path/'root';root.mkdir()
    outside=tmp_path/'private.py';outside.write_text('import kafka\nproducer.send("private")')
    (root/'a.py').symlink_to(outside)
    index=infrastructure_index(root,{'nodes':[{'id':'f','file':'a.py'}]},'s')
    assert not index.facts
    assert index.coverage['unavailableFiles']==1


def test_sql_delete_and_parser_failure_are_explicit(tmp_path):
    """DELETE is a write; malformed source increases coverage errors instead of claiming a clean scan."""
    (tmp_path/'a.py').write_text('import psycopg\ndef f():\n cursor.execute("DELETE FROM assets WHERE id=1")\n')
    (tmp_path/'b.py').write_text('def broken(:')
    graph={'nodes':[{'id':'a','file':'a.py'},{'id':'b','file':'b.py'}]}
    index=infrastructure_index(tmp_path,graph,'s')
    assert index.facts['a.py'][0]['operation']=='writes'
    assert index.coverage['parseErrors']==1


def test_generic_ef_candidates_and_linq_table_call(tmp_path):
    """Concrete GetTable resolves exactly; Set<TEntity> retains each explicit instantiation as a candidate."""
    (tmp_path/'a.cs').write_text('''using Microsoft.EntityFrameworkCore;
class QuoteMap : IEntityTypeConfiguration<Quote> { void Configure(){ b.ToTable("quotes"); } }
class LimitMap : IEntityTypeConfiguration<Limit> { void Configure(){ b.ToTable("limits"); } }
class ReadDbContext { DbSet<Quote> Quotes {get;set;} }
class Source<TEntity,TFact> { void Read(ReadDbContext context){ var rows=context.Set<TEntity>(); } }
class Registry { void F(){ var q=new Source<Quote,QuoteFact>(); var l=new Source<Limit,LimitFact>(); } }
class C(IDbContextFactory<ReadDbContext> factory) { void F(){ var context=factory.CreateDbContext(); var linq=context.CreateLinqToDBContext(); var rows=linq
    .GetTable<Quote>(); } }
''')
    index=infrastructure_index(tmp_path,{'nodes':[{'id':'a','file':'a.cs'}]},'s')
    generic=[f for f in index.facts['a.cs'] if f['basis']=='generic-instantiation-candidate']
    assert {f['name'] for f in generic}=={'quotes','limits'}
    assert all(f['status']=='candidate' and len(f['evidence'])==2 for f in generic)
    exact=[f for f in index.facts['a.cs'] if f['basis']=='typed-table-call']
    assert [f['name'] for f in exact]==['quotes']
    assert exact[0]['status']=='resolved'


def test_sql_strings_comments_and_cte_aliases_are_not_tables(tmp_path):
    """SQL-shaped text inside a value/comment and local CTE names are not external resources."""
    (tmp_path/'a.py').write_text('import psycopg\ndef f():\n cursor.execute("WITH recent AS (SELECT * FROM prices) SELECT \'FROM fake\' FROM recent /* JOIN hidden */ JOIN assets ON true")\n')
    index=infrastructure_index(tmp_path,{'nodes':[{'id':'a','file':'a.py'}]},'s')
    assert {f['name'] for f in index.facts['a.py']}=={'prices','assets'}


def test_node_description_and_path_share_infrastructure_contract(tmp_path):
    """Initial paths and lazy/MCP method descriptions expose the same resource identity and evidence."""
    from atlas.node_description import build_node_description
    from atlas.execution_path import build_execution_path
    (tmp_path/'a.py').write_text('import kafka\ndef send():\n """Publish the prepared assets message."""\n producer.send("assets", value=payload)\n')
    graph={'nodes':[{'id':'send','label':'send()','kind':'function','file':'a.py','line':2}],'edges':[]}
    description=build_node_description(tmp_path,graph,'send','s')
    path=build_execution_path(tmp_path,graph,'send','s')
    assert description['infrastructure']==path['steps'][0]['infrastructure']
    assert description['infrastructure'][0]['name']=='assets'
    assert description['infrastructureTotal']==1
    assert description['infrastructureScope']=='lexical-including-closures'

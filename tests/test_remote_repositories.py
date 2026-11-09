"""Удалённый Git разрешён явно; checkout/hooks и небезопасные архивные пути не выполняются."""
import io
from pathlib import Path
import subprocess
import tarfile
import time
from types import SimpleNamespace
import pytest
from atlas.remote_repositories import validate_remote,extract_archive,RemoteRepositories,RemoteRepositoryError


@pytest.mark.parametrize('url,ref',[
 ('file:///tmp/repo',''),('/tmp/repo',''),('ext::sh -c evil',''),('https://user:secret@host/repo',''),
 ('https://host/repo?token=secret',''),('ssh://user:secret@host/repo',''),
 ('https://host/repo','--upload-pack=evil'),('https://host/repo','main~1'),('https://host/repo','x..y')])
def test_remote_rejects_unsafe_url_and_ref_without_reflecting_credentials(url,ref):
    """Пути, helpers, credentials и Git-выражения не проходят в argv."""
    with pytest.raises(RemoteRepositoryError) as error:
        validate_remote(url,ref)
    assert 'secret' not in str(error.value)


@pytest.mark.parametrize('name',['../outside','/absolute','C:/outside','file:stream','.git/config','a\\b','trailing.'])
def test_remote_archive_rejects_cross_platform_escape(tmp_path,name):
    """Пути недопустимы на любой платформе, включая Windows drives/ADS."""
    archive=tmp_path/'source.tar'
    with tarfile.open(archive,'w') as tar:
        info=tarfile.TarInfo(name);info.size=1;tar.addfile(info,io.BytesIO(b'x'))
    target=tmp_path/'repo';target.mkdir()
    with pytest.raises(RemoteRepositoryError):extract_archive(archive,target)
    assert not (tmp_path/'outside').exists()


class ImmediateIndex:
    """Существующий индексатор заменён только записью вызова, Git остаётся настоящим."""
    def __init__(self,data_dir):
        self.data_dir=data_dir;self.paths=[]
    def list_projects(self):return {'activeJob':None}
    def add(self,path):
        self.paths.append(path)
        return {'project':{'id':'indexed'},'job':{'id':'build'}}
    def get_job(self,job_id):return {'id':job_id,'status':'completed'}


def await_job(manager,job_id):
    """Ожидает ограниченную локальную fixture-операцию без сетевого polling."""
    deadline=time.monotonic()+10
    while time.monotonic()<deadline:
        job=manager.get(job_id)
        if job['status'] in {'completed','failed'}:return job
        time.sleep(.02)
    pytest.fail('remote fixture timed out')


def test_remote_real_git_materializes_sources_without_checkout(tmp_path):
    """Clone/archive/read-tree работают на настоящем локальном Git; подмена URL только в runner fixture."""
    source=tmp_path/'source';source.mkdir()
    def git(*args):
        """Готовит исключительно временный fixture-репозиторий."""
        subprocess.run(['git','-C',str(source),*args],check=True,capture_output=True)
    git('init','-b','main')
    (source/'code.cs').write_text('class Code {}')
    (source/'README.md').write_text('unicode Тест')
    git('add','.');git('-c','user.name=Test','-c','user.email=test@example.invalid','commit','-m','fixture')
    calls=[]
    def runner(command,**options):
        """Заменяет только network URL локальным fixture; остальная production argv неизменна."""
        calls.append((list(command),options))
        assert not options.get('shell',False)
        actual=list(command)
        if 'clone' in actual:
            actual[actual.index('https://example.invalid/repo.git')]=str(source)
            actual[1:1]=['-c','protocol.file.allow=always']
        return subprocess.run(actual,**options)
    index=ImmediateIndex(tmp_path/'data')
    manager=RemoteRepositories(index,tmp_path/'managed',runner=runner)
    try:
        job=await_job(manager,manager.submit('https://example.invalid/repo.git','main')['job']['id'])
        assert job['status']=='completed',job
        clone=Path(job['path'])
        assert (clone/'code.cs').read_text()=='class Code {}'
        assert (clone/'README.md').read_text()=='unicode Тест'
        assert index.paths==[str(clone)]
        command=calls[0][0]
        assert '--no-checkout' in command and '--no-recurse-submodules' in command
        assert command[command.index('--')+1]=='https://example.invalid/repo.git'
        assert all('checkout' not in cmd for cmd,_ in calls)
        assert all(options['env']['GIT_TERMINAL_PROMPT']=='0' for _,options in calls)
    finally:manager.close()


def test_remote_failure_cleans_owned_clone_and_redacts_stderr(tmp_path):
    """Сбой сети не оставляет индекс и не удаляет соседние пользовательские файлы."""
    root=tmp_path/'managed';root.mkdir();(root/'keep.txt').write_text('keep')
    def failed(command,**options):
        """Git stderr содержит секрет, который job не должен возвращать."""
        options['stderr'].write(b'https://user:SECRET@host/repo')
        return SimpleNamespace(returncode=1)
    index=ImmediateIndex(tmp_path/'data');manager=RemoteRepositories(index,root,runner=failed)
    try:
        job=await_job(manager,manager.submit('https://host/repo')['job']['id'])
        assert job['status']=='failed'
        assert 'SECRET' not in str(job)
        assert index.paths==[]
        assert list(root.iterdir())==[root/'keep.txt']
    finally:manager.close()


def test_remote_api_validates_and_exposes_background_job(tmp_path):
    """POST быстро принимает безопасный запрос; статус ошибки доступен без stderr."""
    from fastapi.testclient import TestClient
    from atlas.server import create_app
    app=create_app(tmp_path/'data');calls=[]
    def fail(command,**options):
        """Сбой клонирования без сети для проверки HTTP wiring."""
        calls.append(command)
        return SimpleNamespace(returncode=1)
    app.state.remote_repositories.runner=fail
    with TestClient(app,base_url='http://127.0.0.1:8765') as client:
        assert client.post('/api/remote-repositories',json={'url':'file:///tmp/repo'}).status_code==400
        assert calls==[]
        assert client.post('/api/remote-repositories',json={'url':'https://host/repo','shell':'evil'}).status_code==422
        response=client.post('/api/remote-repositories',json={'url':'https://host/repo','ref':'main'})
        assert response.status_code==202,response.text
        job_id=response.json()['job']['id']
        terminal=await_job(app.state.remote_repositories,job_id)
        assert terminal['status']=='failed'
        read=client.get('/api/remote-repositories/jobs/'+job_id)
        assert read.status_code==200 and read.json()['status']=='failed'
        assert client.get('/api/remote-repositories/jobs/missing').status_code==404

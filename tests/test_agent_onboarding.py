"""Техническая ссылка раскрывает полные навыки и настройки без JS и записи в IDE."""
from html.parser import HTMLParser
from io import BytesIO
import json
import re
import tomllib
from zipfile import ZipFile

from fastapi.testclient import TestClient
import pytest

from atlas.agent_setup import agent_mcp_config, codex_config
from atlas.server import create_app


class GuideLink(HTMLParser):
    """Выбирает machine-readable вход из HTML, не выполняя script."""
    def handle_starttag(self, tag, attrs):
        values = dict(attrs)
        if tag == "link" and values.get("rel") == "alternate" and values.get("type") == "text/markdown":
            self.href = values["href"]


def test_page_link_discovers_guide_configuration_and_complete_skills(tmp_path):
    """Все ссылки входной инструкции разрешаются и сохраняют references в ZIP."""
    with TestClient(create_app(tmp_path), base_url="http://127.0.0.1:8877") as client:
        page = client.get("/technical.html")
        parser = GuideLink()
        parser.feed(page.text)
        guide = client.get(parser.href)
        assert guide.status_code == 200
        assert guide.headers["content-type"].startswith("text/markdown")
        assert "content-disposition" not in guide.headers
        catalog = client.get("/api/agent-setup/skills").json()
        assert catalog["requiresNewChat"]
        assert {s["name"] for s in catalog["skills"]} == {
            "code-atlas", "code-atlas-engines", "code-atlas-extensions", "code-atlas-mods"}
        for skill in catalog["skills"]:
            assert "http://127.0.0.1:8877" + skill["url"] in guide.text
            with ZipFile(BytesIO(client.get(skill["zipUrl"]).content)) as archive:
                assert sorted(archive.namelist()) == sorted(f"{skill['name']}/{r['path']}" for r in skill["resources"])
                for resource in skill["resources"]:
                    response = client.get(resource["url"])
                    assert response.status_code == 200
                    assert archive.read(f"{skill['name']}/{resource['path']}") == response.content
        # Даже только текстовый клиент получает рабочие ссылки с реальным портом.
        links = set(re.findall(r"http://127\.0\.0\.1:8877/[^\s]+", guide.text))
        for link in links:
            assert client.get(link).status_code == 200, link
        assert not (tmp_path / ".cursor").exists() and not (tmp_path / ".agents").exists()


def test_project_guide_preserves_scope_in_both_editor_formats(tmp_path):
    """TOML и JSON указывают один порт/проект и не теряют аргументы прокси."""
    app = create_app(tmp_path)
    app.state.store.projects["chosen"] = {"id": "chosen", "name": "Chosen", "path": str(tmp_path), "status": "pending"}
    with TestClient(app, base_url="http://127.0.0.1:8877") as client:
        payload = client.get("/api/agent-setup?projectId=chosen").json()
        cursor = client.get(payload["downloads"]["config"]).json()["mcpServers"]["code-atlas-agent"]
        codex = tomllib.loads(client.get(payload["downloads"]["codex"]).text)["mcp_servers"]["code-atlas-agent"]
        assert codex == cursor
        assert "chosen" in codex["args"] and "8877" in codex["args"]
        guide = client.get(payload["guideUrl"])
        assert json.dumps(payload["mcpConfig"], indent=2, ensure_ascii=False) in guide.text
        assert payload["codexConfig"].rstrip() in guide.text
        assert payload["requiresNewChat"]
        assert client.get("/agent-setup.md?projectId=unknown").status_code == 404
        assert client.get("/api/agent-setup/config?format=ini").status_code == 422


@pytest.mark.parametrize("name,resource", [("../../atlas", "SKILL.md"), ("code-atlas", "../../config.toml"),
                                           ("code-atlas-extensions", "/etc/passwd")])
def test_skill_download_cannot_read_arbitrary_files(tmp_path, name, resource):
    """Каталог не становится файловым API при подмене имени или supporting resource."""
    with TestClient(create_app(tmp_path), base_url="http://127.0.0.1:8877") as client:
        response = client.get("/api/agent-setup/skill", params={"name": name, "resource": resource})
        assert response.status_code == 404
        assert client.get("/api/agent-setup/skill.zip", params={"name": "../../atlas"}).status_code == 404


def test_toml_preserves_special_characters_and_guide_rejects_nonlocal_origin(tmp_path):
    """Пути с кавычками и переносами остаются значениями; forwarded host игнорируется."""
    config = agent_mcp_config(8877, "project")
    entry = config["mcpServers"]["code-atlas-agent"]
    entry["command"] = '/path with "quotes"/runtime'
    entry["env"]["PYTHONPATH"] = "C:\\path\\Atlas\nДругой путь"
    assert tomllib.loads(codex_config(config))["mcp_servers"]["code-atlas-agent"] == entry
    with TestClient(create_app(tmp_path), base_url="http://127.0.0.1:8877") as client:
        text = client.get("/agent-setup.md", headers={"x-forwarded-host": "evil.invalid:9000"}).text
        assert "evil.invalid" not in text
        assert "http://127.0.0.1:8877" in text
        assert client.get("http://evil.invalid:8877/agent-setup.md").status_code == 403

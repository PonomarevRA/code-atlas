"""Запуск приложения и экспорт stdio MCP-конфигурации для локального клиента."""

import argparse
import json
import os
from pathlib import Path
import sys
import threading

from .process_lock import ProcessLock
from .browser_launch import open_when_ready
from .runtime_paths import APP_VERSION, backup_data, default_data_dir, ensure_data_schema, migrate_legacy_data, worker_executable

ROOT = Path(__file__).resolve().parent.parent


def main():
    """Запускает единственный локальный сервер; интернет для запуска не нужен."""
    parser = argparse.ArgumentParser(description="Code Atlas — локальная карта кода")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--data-dir", type=Path, default=None)
    parser.add_argument("--version", action="version", version=f"Code Atlas {APP_VERSION}")
    parser.add_argument("--migrate-from", type=Path, help="Скопировать старые данные в новый --data-dir и завершить")
    parser.add_argument("--backup-data", action="store_true", help="Создать ZIP резервной копии данных и завершить")
    parser.add_argument('--prepare-update', action='store_true', help='Проверить остановку сервера и совместимость данных без их копирования')
    parser.add_argument('--native-document-check', action='store_true', help='Проверить готовые native readers на изолированных fixtures')
    storage = parser.add_mutually_exclusive_group()
    storage.add_argument('--storage-report', action='store_true', help='Показать размер данных и восстанавливаемых кешей')
    storage.add_argument('--optimize-storage', action='store_true', help='Удалить только восстанавливаемые кеши; Atlas должен быть остановлен')
    browser = parser.add_mutually_exclusive_group()
    browser.add_argument("--open", dest="open", action="store_true", help="Явно открыть браузер после старта сервера")
    browser.add_argument("--no-open", dest="open", action="store_false", help="Запустить сервер без браузера")
    parser.add_argument("--enable-desktop-automation", action="store_true",
                         help="Разрешить локальным MCP-клиентам управление окном Atlas")
    browser.add_argument("--desktop", action="store_true", help="Открыть Atlas в отдельном окне приложения")
    parser.set_defaults(open=False)
    parser.add_argument("--mcp-config", metavar="PROJECT_ID", help="Вывести MCP-конфигурацию построенного проекта")
    parser.add_argument("--agent-mcp-config", action="store_true", help="Вывести MCP-конфигурацию агентских сохранённых видов")
    parser.add_argument("--project-id", help="Ограничить агентский MCP выбранным ID проекта")
    args = parser.parse_args()
    if args.enable_desktop_automation and not args.desktop:
        parser.error("--enable-desktop-automation требует --desktop")
    explicit_data_dir = args.data_dir is not None
    args.data_dir = (args.data_dir or default_data_dir()).expanduser().absolute()
    if args.native_document_check:
        from .native_documents_check import check_native_documents
        try:
            print(json.dumps(check_native_documents(), ensure_ascii=False))
        except Exception as error:
            print(json.dumps({'status': 'FAIL', 'error': str(error)}, ensure_ascii=False))
            raise SystemExit(1) from None
        return
    if args.prepare_update:
        from .update_preflight import prepare_update
        try:
            print(json.dumps(prepare_update(args.data_dir), ensure_ascii=False))
        except (RuntimeError, OSError) as error:
            raise SystemExit(str(error)) from None
        return
    if args.storage_report or args.optimize_storage:
        from .project_storage import storage_report, optimize_storage
        try:
            result = optimize_storage(args.data_dir) if args.optimize_storage else storage_report(args.data_dir)
        except (RuntimeError, OSError, ValueError) as error:
            raise SystemExit(str(error)) from None
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return
    if args.migrate_from:
        try:
            marker = migrate_legacy_data(args.migrate_from, args.data_dir)
        except RuntimeError as error:
            raise SystemExit(str(error)) from None
        print(json.dumps(marker, ensure_ascii=False, indent=2))
        return
    if args.backup_data:
        try:
            print(backup_data(args.data_dir))
        except RuntimeError as error:
            raise SystemExit(str(error)) from None
        return
    if args.agent_mcp_config:
        if not 1024 <= args.port <= 65535:
            parser.error("Порт должен быть от 1024 до 65535")
        export_agent_mcp(args.port, args.project_id)
        return
    if args.mcp_config:
        export_mcp(args.data_dir, args.mcp_config)
        return
    if not 1024 <= args.port <= 65535:
        parser.error("Порт должен быть от 1024 до 65535")
    legacy = ROOT / ".atlas"
    if not explicit_data_dir and not args.data_dir.exists() and legacy.is_dir():
        try:
            migrate_legacy_data(legacy, args.data_dir)
        except RuntimeError as error:
            raise SystemExit(str(error)) from None
    args.data_dir.mkdir(parents=True, exist_ok=True)
    try:
        with ProcessLock(args.data_dir / "server.lock"):
            ensure_data_schema(args.data_dir)
            os.environ["ATLAS_DATA_DIR"] = str(args.data_dir.resolve())
            url = f"http://127.0.0.1:{args.port}"
            print(f"Code Atlas {APP_VERSION}: {url}\nДанные: {args.data_dir.resolve()}\nОстановка: Ctrl+C", flush=True)
            import uvicorn
            application, factory = "atlas.server:create_app", True
            automation = None
            if args.desktop:
                from .server import create_app
                if args.enable_desktop_automation:
                    from .desktop_automation import DesktopAutomation
                    automation = DesktopAutomation()
                application = create_app(desktop_automation=automation) if automation is not None else create_app()
                factory = False
            server = uvicorn.Server(uvicorn.Config(application, factory=factory, host="127.0.0.1", port=args.port,
                                                   timeout_graceful_shutdown=10))
            if args.desktop:
                from .desktop_launch import run_desktop
                options = {"automation": automation} if automation is not None else {}
                run_desktop(server, url, args.data_dir, request_shutdown=application.state.request_shutdown, **options)
                return
            stopped = threading.Event()
            if args.open:
                threading.Thread(target=open_when_ready, args=(server, stopped, url), daemon=True).start()
            try:
                server.run()
            except KeyboardInterrupt:
                # Uvicorn завершил shutdown и повторно передал пользовательский SIGINT.
                # Frozen launcher не должен показывать его как ошибку запуска.
                pass
            finally:
                stopped.set()
    except RuntimeError as error:
        raise SystemExit(str(error)) from None


def export_agent_mcp(port: int, project_id: str | None = None) -> None:
    """Печатает конфигурацию stdio-прокси к уже работающему локальному Atlas."""
    from .agent_setup import agent_mcp_config
    print(json.dumps(agent_mcp_config(port, project_id), indent=2, ensure_ascii=False))


def export_mcp(data_dir: Path, project_id: str):
    """Печатает конфигурацию Graphify MCP для последнего успешного снимка."""
    from .store import ProjectStore
    store = ProjectStore(data_dir)
    try:
        graph_path = store.published_mcp_graph(project_id)
        from .integrations.graphify.runtime import command as provider_command
        with store.graphify_runtime.activate():
            command = provider_command("graphify.serve", [str(graph_path)])
        config = {"mcpServers": {"code-atlas": {
            "command": command[0],
            "args": command[1:],
            "env": {"PYTHONPATH": str(ROOT)},
        }}}
        print(json.dumps(config, indent=2, ensure_ascii=False))
    finally:
        store.close()


if __name__ == "__main__":
    main()

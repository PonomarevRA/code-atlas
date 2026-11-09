"""Единый frozen entrypoint и совместимый dispatch локальных дочерних процессов."""

import multiprocessing
from pathlib import Path
import runpy
import sys


def configure_windowed_streams() -> None:
    """Сохраняет startup ошибки Windows GUI, где stdout/stderr отсутствуют."""
    if sys.stdout is not None and sys.stderr is not None:
        return
    from atlas.runtime_paths import default_data_dir
    data_dir = default_data_dir()
    data_dir.mkdir(parents=True, exist_ok=True)
    path = data_dir / "desktop.log"
    if path.exists() and path.stat().st_size > 2_000_000:
        path.replace(data_dir / "desktop.previous.log")
    stream = path.open("a", encoding="utf-8", buffering=1)
    if sys.stdout is None:
        sys.stdout = stream
    if sys.stderr is None:
        sys.stderr = stream


def main() -> None:
    """Принимает только внутренние -m модули; обычные аргументы передаются Atlas CLI."""
    multiprocessing.freeze_support()
    configure_windowed_streams()
    if len(sys.argv) > 2 and sys.argv[1] == "-m":
        module = sys.argv[2]
        if module not in {"atlas.offline_runner", "atlas.agent_mcp", "atlas.mod_worker", "atlas.architecture_cli"}:
            raise SystemExit("Этот standalone runtime запускает только внутренние модули Atlas.")
        sys.argv = [module, *sys.argv[3:]]
        runpy.run_module(module, run_name="__main__", alter_sys=True)
        return
    if len(sys.argv) > 1 and Path(sys.argv[1]).name == "snapshot_png_worker.py":
        sys.argv = ["atlas.snapshot_png_worker", *sys.argv[2:]]
        runpy.run_module("atlas.snapshot_png_worker", run_name="__main__", alter_sys=True)
        return
    # Finder/ярлык запускает приложение без аргументов: отдельное desktop окно.
    if len(sys.argv) == 1:
        sys.argv.append("--desktop")
    from atlas.cli import main as cli_main
    cli_main()


if __name__ == "__main__":
    main()

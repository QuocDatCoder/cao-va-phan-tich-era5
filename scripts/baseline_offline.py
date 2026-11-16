"""Capture a reproducible offline baseline without loading local credentials.

Run from the repository root using .venv/Scripts/python.exe scripts/baseline_offline.py.
Checks use isolated data/output directories; application code is unchanged.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import importlib.metadata
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import time
import urllib.request

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def isolated_settings(scratch: Path, allow_loopback: bool) -> list[str]:
    """Block credential-file reads, CDS clients, and outbound network traffic."""
    scratch.mkdir(parents=True, exist_ok=True)
    os.environ.update({
        "CLIMATE_PROVIDER": "mock", "ERA5_SYNC_ENABLED": "false",
        "RUN_CDS_INTEGRATION": "0", "CDS_URL": "", "CDS_API_KEY": "",
        "DATA_DIR": str(scratch / "data"),
        "CLIMATE_DATA_DB_PATH": str(scratch / "climate.sqlite3"),
        "JOB_METADATA_DB_PATH": str(scratch / "jobs.sqlite3"),
        "STREAMLIT_BROWSER_GATHER_USAGE_STATS": "false",
    })
    blocked: list[str] = []

    def audit(event, args):
        if event == "open" and isinstance(args[0], (str, bytes, os.PathLike)):
            name = Path(os.fsdecode(args[0])).name.lower()
            if name in {".env", ".cdsapirc"}:
                blocked.append("credential_file_read")
                raise PermissionError("Offline baseline disallows credential-file reads")
        if event == "socket.connect":
            address = args[1]
            host = address[0] if isinstance(address, tuple) else None
            if not (allow_loopback and host in {"127.0.0.1", "::1", "localhost"}):
                blocked.append("outbound_connection")
                raise PermissionError("Offline baseline blocks outbound connections")

    sys.addaudithook(audit)
    from backend.app.core.config import Settings, get_settings
    Settings.model_config = {**Settings.model_config, "env_file": None}
    get_settings.cache_clear()
    import cdsapi

    def no_cds(*args, **kwargs):
        blocked.append("cds_client")
        raise AssertionError("CDS client is disabled in the offline baseline")

    cdsapi.Client = no_cds
    return blocked


def child(mode: str, output: Path, port: int, api_port: int) -> int:
    # Windows asyncio socketpair uses loopback even for in-process TestClient.
    blocked = isolated_settings(output / "scratch", allow_loopback=True)
    if mode == "tests":
        import pytest
        code = pytest.main([
            "tests", "-q", "--tb=short", "-ra", "-p", "no:cacheprovider",
            "--basetemp", str(output / "pytest-temp"),
            "--junitxml", str(output / "pytest.xml"),
        ])
        (output / "test_safety.json").write_text(json.dumps({
            "env_file_loading": False, "cds_client_enabled": False,
            "outbound_connections_enabled": False, "loopback_enabled": True,
            "blocked_events": blocked,
        }, indent=2), encoding="utf-8")
        return int(code)
    if mode == "backend":
        import uvicorn
        uvicorn.run("backend.app.main:app", host="127.0.0.1", port=port)
        return 0
    os.environ["API_BASE_URL"] = f"http://127.0.0.1:{api_port}"
    if mode == "frontend":
        from streamlit.web import cli
        sys.argv = ["streamlit", "run", str(ROOT / "app.py"),
                    "--server.address=127.0.0.1", f"--server.port={port}",
                    "--server.headless=true", "--browser.gatherUsageStats=false"]
        return int(cli.main() or 0)
    from streamlit.testing.v1 import AppTest
    app = AppTest.from_file(str(ROOT / "app.py"), default_timeout=30).run()
    report = {
        "exceptions": [str(x.message) for x in app.exception],
        "errors": [str(x.value) for x in app.error],
        "navigation_options": [list(x.options) for x in app.pills],
        "configured_provider": "mock", "blocked_events": blocked, "checks": [],
    }
    # Seed only the isolated backend: production UI deliberately has no Demo option.
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    payload = dict(source='mock', variable='temperature',
                   location=dict(latitude=10.8231, longitude=106.6297),
                   start_date='2025-01-01', end_date='2025-01-01', frequency='hourly')
    request = urllib.request.Request(f'http://127.0.0.1:{api_port}/api/v1/downloads',
                                    json.dumps(payload).encode(), {'Content-Type': 'application/json'})
    with opener.open(request, timeout=10) as response:
        created = json.load(response)
    mock_id = created['job_id']
    for attempt in range(30):
        with opener.open(f"http://127.0.0.1:{api_port}/api/v1/jobs/{mock_id}", timeout=5) as response:
            mock_job = json.load(response)
        if mock_job['status'] not in {'queued', 'running'}:
            break
        time.sleep(.1)
    report['mock_collection'] = {key: mock_job[key] for key in ['source', 'dataset', 'status', 'spatial_mode']}
    for section_name, prefix in [('Dữ liệu đã lưu','stored'), ('Biểu đồ','chart')]:
        app.pills(key='workflow_section').set_value(section_name).run()
        action = '_draw_' if prefix == 'chart' else '_view_'
        app.button(key=prefix+action+mock_id).click().run()
        app.button(key=prefix+'_selected_submit').click().run()
        if prefix == 'chart':
            app.session_state['timeseries_table_panel'] = True
            app.run()
        report['checks'].append(dict(section=section_name,
            exceptions=[str(x.message) for x in app.exception],
            errors=[str(x.value) for x in app.error],
            csv_available=any(d.key=='download_timeseries_csv' for d in app.get('download_button'))))
    report['passed'] = (not report['exceptions'] and not report['errors']
                        and bool(report['navigation_options'])
                        and all(not c['exceptions'] and not c['errors'] and c['csv_available'] for c in report['checks'])
                        and report['mock_collection'] == dict(source='mock', dataset='mock', status='completed', spatial_mode='point'))
    (output/'frontend_apptest.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps(report,ensure_ascii=False,indent=2))
    return 0 if report['passed'] else 1


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def git(*args) -> str:
    return subprocess.check_output(["git", *args], cwd=ROOT, text=True, encoding="utf-8").strip()


def inventory() -> dict:
    # Metadata only: never open .env or inspect its contents, never open user databases.
    paths = [ROOT / ".env"]
    data = ROOT / "data"
    if data.exists():
        # SQLite removes/recreates these transient coordination files when connections close.
        # They do not represent persistent user inputs; keep tracking the main DB and raw files.
        paths.extend(p for p in data.rglob("*") if p.is_file()
                     and not p.name.endswith(('-wal', '-shm')))
    return {p.relative_to(ROOT).as_posix(): {
        "size": p.stat().st_size, "mtime_ns": p.stat().st_mtime_ns,
    } for p in paths if p.is_file()}


def orchestrate(output: Path) -> int:
    output.mkdir(parents=True, exist_ok=False)
    metadata = {
        "started_utc": datetime.now(timezone.utc).isoformat(),
        "commit": git("rev-parse", "HEAD"), "branch": git("branch", "--show-current"),
        "git_status_before": git("status", "--short"), "python": sys.version,
        "versions": {p: importlib.metadata.version(p) for p in
                     ["fastapi", "uvicorn", "streamlit", "pytest", "cdsapi", "xarray"]},
        "input_inventory_before": inventory(),
        "scope": "Offline tests and isolated mock startup only; no live CDS checks",
    }
    base = [sys.executable, "-X", "utf8", str(Path(__file__).resolve()),
            "--output-dir", str(output)]
    env = dict(os.environ, PYTHONIOENCODING="utf-8", PYTHONUTF8="1")
    with (output / "pytest.log").open("w", encoding="utf-8") as log:
        result = subprocess.run(base + ["--child", "tests"], cwd=ROOT,
                                env=env, stdout=log, stderr=subprocess.STDOUT)
        metadata["pytest_exit_code"] = result.returncode
    api_port, ui_port = free_port(), free_port()
    processes = []
    logs = []
    startup = {"backend_port": api_port, "frontend_port": ui_port}
    try:
        for mode, port in [("backend", api_port), ("frontend", ui_port)]:
            log = (output / f"{mode}.log").open("w", encoding="utf-8")
            logs.append(log)
            processes.append(subprocess.Popen(base + ["--child", mode, "--port", str(port),
                                                       "--api-port", str(api_port)],
                                              cwd=ROOT, env=env, stdout=log, stderr=subprocess.STDOUT))
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        for name, url in [
            ("backend_health", f"http://127.0.0.1:{api_port}/health"),
            ("backend_docs", f"http://127.0.0.1:{api_port}/docs"),
            ("frontend_health", f"http://127.0.0.1:{ui_port}/_stcore/health"),
            ("frontend_page", f"http://127.0.0.1:{ui_port}/"),
        ]:
            deadline = time.monotonic() + 30
            while True:
                try:
                    with opener.open(url, timeout=2) as response:
                        text = response.read().decode("utf-8")
                        startup[name] = {"status": response.status,
                                         "body": text if "health" in name else "HTML received"}
                    break
                except Exception as exc:
                    if time.monotonic() > deadline:
                        startup[name] = {"error": type(exc).__name__}
                        break
                    time.sleep(0.25)
        with (output / "frontend_apptest.log").open("w", encoding="utf-8") as log:
            result = subprocess.run(base + ["--child", "frontend-check", "--api-port", str(api_port)],
                                    cwd=ROOT, env=env, stdout=log, stderr=subprocess.STDOUT, timeout=150)
            startup["frontend_apptest_exit_code"] = result.returncode
    finally:
        for process in processes:
            process.terminate()
        for process in processes:
            try:
                process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=10)
        for log in logs:
            log.close()
        metadata["startup"] = startup
        metadata["input_inventory_after"] = inventory()
        metadata["user_inputs_unchanged"] = (metadata["input_inventory_before"]
                                             == metadata["input_inventory_after"])
        metadata["finished_utc"] = datetime.now(timezone.utc).isoformat()
        (output / "baseline.json").write_text(
            json.dumps(metadata, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"output": str(output), "pytest_exit_code": metadata["pytest_exit_code"],
                      "startup": startup, "user_inputs_unchanged": metadata["user_inputs_unchanged"]},
                     ensure_ascii=False, indent=2))
    healthy = all(startup.get(key, {}).get("status") == 200 for key in
                  ["backend_health", "backend_docs", "frontend_health", "frontend_page"])
    return 0 if (metadata["pytest_exit_code"] == 0 and healthy
                 and startup.get("frontend_apptest_exit_code") == 0
                 and metadata["user_inputs_unchanged"]) else 1


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--child", choices=["tests", "backend", "frontend", "frontend-check"])
    parser.add_argument("--output-dir", type=Path)
    parser.add_argument("--port", type=int, default=0)
    parser.add_argument("--api-port", type=int, default=0)
    args = parser.parse_args()
    output = (args.output_dir or (ROOT / "output/baseline" /
              datetime.now().strftime("%Y%m%d-%H%M%S"))).resolve()
    if args.child:
        raise SystemExit(child(args.child, output, args.port, args.api_port))
    raise SystemExit(orchestrate(output))

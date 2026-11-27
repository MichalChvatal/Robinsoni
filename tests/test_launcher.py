import json
import os
import socket
import subprocess
import sys
from pathlib import Path

from mr_quality_monitor.services.receiver_health import read_health


def test_launcher_reuses_services_and_stops_them(tmp_path):
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]
    config = tmp_path / "app.yaml"
    config.write_text(json.dumps({"storage_root": "store", "port": port}), encoding="utf-8")
    protocol = Path("config/protocols/demo_mr.yaml").resolve()
    root = tmp_path / "store"

    def invoke(action):
        command = [
            sys.executable,
            "-m",
            "mr_quality_monitor.launcher",
            action,
            "--config",
            str(config),
            "--protocol",
            str(protocol),
            "--no-window",
        ]
        env = {**os.environ, "PYTHONPATH": str(Path("backend").resolve())}
        return subprocess.run(command, capture_output=True, timeout=30, env=env)

    try:
        start = invoke("start")
        assert start.returncode == 0, start.stderr
        first = read_health(root)
        assert first["observed_state"] == "listening"
        assert invoke("start").returncode == 0
        assert read_health(root)["run_id"] == first["run_id"]
        assert (root / "logs/analysis.log").read_text().count("analysis_started") == 1
    finally:
        assert invoke("stop").returncode == 0
    assert read_health(root)["observed_state"] == "stopped"

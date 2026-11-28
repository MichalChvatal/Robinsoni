import json
import os
import socket
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

from mr_quality_monitor import launcher
from mr_quality_monitor.dicom.storage import Storage
from mr_quality_monitor.gui.view_data import MonitorReader
from mr_quality_monitor.services.receiver_health import read_health


def test_launcher_reuses_services_and_stops_them(tmp_path):
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]
    config = tmp_path / "app.yaml"
    config.write_text(json.dumps({"storage_root": "store", "port": port}), encoding="utf-8")
    protocol = Path("tests/fixtures/protocols/demo_mr.yaml").resolve()
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


def test_reopening_desktop_starts_with_empty_study(tmp_path, image, monkeypatch):
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]
    config = tmp_path / "app.yaml"
    config.write_text(
        json.dumps({"storage_root": "store", "port": port, "single_active_study": True}),
        encoding="utf-8",
    )
    root = tmp_path / "store"
    reader = MonitorReader(root, single_active_study=True)
    launch = launcher.launch
    desktop_starts = []
    sender_starts = []

    def launch_with_test_windows(module, *args):
        if module == "mr_quality_monitor.gui":
            desktop_starts.append((read_health(root)["run_id"], reader.read()))
            return SimpleNamespace(poll=lambda: 0)  # Window has closed.
        if module == "mr_quality_monitor.gui.sender":
            sender_starts.append(read_health(root)["run_id"])
            return SimpleNamespace(poll=lambda: 0)
        return launch(module, *args)

    def invoke(action):
        monkeypatch.setattr(
            sys, "argv",
            [
                "launcher", action, "--config", str(config),
                "--protocol", str(Path("config/protocols").resolve()),
            ],
        )
        return launcher.main()

    monkeypatch.setenv("PYTHONPATH", str(Path("backend").resolve()))
    monkeypatch.setattr(launcher, "launch", launch_with_test_windows)
    try:
        assert invoke("start") == 0
        first_id, initial = desktop_starts[0]
        assert initial.study_uid is None
        storage = Storage(root, 0, single_active_study=True)
        try:
            syntax = str(image.file_meta.TransferSyntaxUID)
            assert storage.receive(image, syntax, "MR_TEST", "127.0.0.1") == "stored"
        finally:
            storage.close()
        assert reader.read().study_uid == str(image.StudyInstanceUID)
        assert invoke("send") == 0
        assert sender_starts == [first_id]
        assert reader.read().study_uid == str(image.StudyInstanceUID)

        assert invoke("start") == 0
        second_id, reopened = desktop_starts[1]
        assert second_id != first_id
        assert reopened.study_uid is None and not reopened.rows and reopened.completion is None
        assert reopened.message == "Čeká na první data z MR."
        assert not list((root / "objects").glob("*.dcm"))
    finally:
        assert invoke("stop") == 0

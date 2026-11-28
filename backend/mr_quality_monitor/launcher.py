"""Start local backend processes in the background and open a desktop window."""

import argparse
import os
import subprocess
import sys
from contextlib import ExitStack
from pathlib import Path
from time import monotonic, sleep

from .config import load_config
from .process_lock import ProcessLock
from .services.receiver_health import read_health


def running(root: Path, service: str) -> bool:
    lock = ProcessLock(root, service)
    try:
        lock.acquire()
    except RuntimeError:
        return True
    lock.release()
    return False


def acquire_launcher(lock: ProcessLock) -> bool:
    try:
        lock.acquire()
    except RuntimeError:
        return False
    return True


def launch(module: str, config: Path, log: Path, *arguments: str) -> subprocess.Popen:
    with log.open("ab") as output:
        return subprocess.Popen(
            [sys.executable, "-m", module, "--config", str(config), *arguments],
            stdin=subprocess.DEVNULL,
            stdout=output,
            stderr=subprocess.STDOUT,
            creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
        )


def wait_ready(process: subprocess.Popen | None, ready, service: str) -> None:
    deadline = monotonic() + 180
    while monotonic() < deadline:
        if process is not None and process.poll() is not None:
            raise RuntimeError(f"{service}: spuštění se nezdařilo.")
        if ready():
            return
        sleep(0.25)
    raise RuntimeError(f"{service}: vypršel čas čekání na spuštění.")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("start", "send", "stop"))
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--protocol", type=Path, required=True)
    parser.add_argument("--no-window", action="store_true", help="Start only background services")
    args = parser.parse_args()
    try:
        config = load_config(args.config)
        root = config.storage_root
        control = root / "launcher"
        control.mkdir(parents=True, exist_ok=True)
        logs = root / "logs"
        logs.mkdir(exist_ok=True)
        with ExitStack() as stack:
            lock = ProcessLock(root, "launcher")
            wait_ready(None, lambda: acquire_launcher(lock), "Spouštěč")
            stack.callback(lock.release)
            receiver_stop = control / "receiver.stop"
            analysis_stop = control / "analysis.stop"
            if args.action == "stop":
                for service, stop_file in (
                    ("receiver", receiver_stop),
                    ("analysis", analysis_stop),
                ):
                    managed = control / f"{service}.managed"
                    if managed.is_file():
                        stop_file.touch()
                for service in ("receiver", "analysis"):
                    managed = control / f"{service}.managed"
                    if managed.is_file():
                        wait_ready(None, lambda: not running(root, service), service)
                        managed.unlink()
                print("Příjem a analýza spuštěné zástupcem jsou zastavené.")
                return 0

            def receiver_ready():
                status = read_health(root)
                return (
                    status["observed_state"] == "listening"
                    and status["host"] == str(config.host)
                    and status["port"] == config.port
                    and status["ae_title"] == config.ae_title
                    and status["run_id"] != old_run
                )

            old_run = None
            receiver = None
            if not running(root, "receiver"):
                old_run = read_health(root).get("run_id")
                receiver_stop.unlink(missing_ok=True)
                receiver = launch(
                    "mr_quality_monitor.cli",
                    args.config,
                    logs / "launcher-receiver.log",
                    "--stop-file",
                    str(receiver_stop),
                )
                (control / "receiver.managed").touch()
            wait_ready(receiver, receiver_ready, "DICOM příjem")

            if not running(root, "analysis"):
                analysis_stop.unlink(missing_ok=True)
                ready_file = control / "analysis.ready"
                ready_file.unlink(missing_ok=True)
                analysis = launch(
                    "mr_quality_monitor.worker_cli",
                    args.config,
                    logs / "launcher-analysis.log",
                    "--protocol",
                    str(args.protocol),
                    "--stop-file",
                    str(analysis_stop),
                    "--ready-file",
                    str(ready_file),
                )
                (control / "analysis.managed").touch()
                wait_ready(analysis, ready_file.is_file, "Analýza")

            if args.no_window:
                return 0
            module = "mr_quality_monitor.gui"
            if args.action == "send":
                module += ".sender"
            window = launch(module, args.config, logs / f"launcher-{args.action}.log")
            sleep(0.5)
            if window.poll() not in (None, 0):
                raise RuntimeError("Okno aplikace se nepodařilo otevřít.")
    except Exception as error:
        message = str(error) if isinstance(error, RuntimeError) else type(error).__name__
        print(
            f"{message} Podrobnosti jsou v adresáři logs uvnitř úložiště aplikace.", file=sys.stderr
        )
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

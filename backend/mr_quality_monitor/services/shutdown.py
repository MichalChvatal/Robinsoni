"""Normal shutdown signals and an optional local request from the Windows launcher."""

import signal
from pathlib import Path
from threading import Event, Thread


def shutdown_event(stop_file: Path | None) -> Event:
    stopped = Event()
    signal.signal(signal.SIGINT, lambda *_args: stopped.set())
    signal.signal(signal.SIGTERM, lambda *_args: stopped.set())
    if stop_file is not None:

        def watch() -> None:
            while not stopped.is_set():
                if stop_file.exists():
                    stopped.set()
                stopped.wait(0.25)

        Thread(target=watch, name="launcher-shutdown", daemon=True).start()
    return stopped

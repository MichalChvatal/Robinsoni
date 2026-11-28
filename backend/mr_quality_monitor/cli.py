"""Console receiver entry point; no GUI or clinical interpretation."""

import argparse
import logging
import time
import warnings
from logging.handlers import RotatingFileHandler
from pathlib import Path

from .config import load_config
from .dicom.listener import Listener
from .services.shutdown import shutdown_event


def configure_logging(root: Path, *, filename: str = "receiver.log", console: bool = True) -> None:
    """Rotate sanitised application events; suppress dataset-bearing library logs."""
    log_dir = root / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    logger = logging.getLogger("mr_quality_monitor")
    logger.setLevel(logging.INFO)
    formatter = logging.Formatter(
        "%(asctime)sZ %(levelname)s %(name)s %(message)s", datefmt="%Y-%m-%dT%H:%M:%S"
    )
    formatter.converter = time.gmtime
    file_handler = RotatingFileHandler(
        log_dir / filename, maxBytes=2_000_000, backupCount=5, encoding="utf-8"
    )
    handlers = (file_handler, logging.StreamHandler()) if console else (file_handler,)
    for handler in handlers:
        handler.setFormatter(formatter)
        logger.addHandler(handler)
    # DICOM library warnings may include invalid element values; only our fixed codes are logged.
    for name in ("pydicom", "pynetdicom"):
        library_logger = logging.getLogger(name)
        library_logger.handlers = [logging.NullHandler()]
        library_logger.propagate = False
        library_logger.setLevel(logging.CRITICAL + 1)
        warnings.filterwarnings("ignore", module=rf"{name}(\..*)?")


def main() -> int:
    parser = argparse.ArgumentParser(description="Local MR DICOM receiver (stage 2)")
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--stop-file", type=Path, help=argparse.SUPPRESS)
    args = parser.parse_args()
    try:
        config = load_config(args.config)
    except Exception as error:
        print(f"Configuration could not be loaded ({type(error).__name__})")
        return 2
    try:
        configure_logging(config.storage_root, console=args.stop_file is None)
    except OSError:
        print("Log directory is not writable")
        return 2
    stopped = shutdown_event(args.stop_file)
    logger = logging.getLogger("mr_quality_monitor.receiver")
    listener = Listener(config)
    try:
        listener.start()
        stopped.wait()
    except Exception as error:
        logger.error("receiver_failed type=%s", type(error).__name__)
        return 1
    finally:
        listener.stop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

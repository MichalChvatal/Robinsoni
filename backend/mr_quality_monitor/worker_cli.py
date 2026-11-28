"""Independent local MR analysis command."""

import argparse
import logging
from pathlib import Path

from .cli import configure_logging
from .config import load_config
from .protocols.manager import load_protocol_candidates
from .services.analysis_worker import open_worker
from .services.shutdown import shutdown_event


def main() -> int:
    parser = argparse.ArgumentParser(description="Local MR quality analysis worker")
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--protocol", type=Path, required=True)
    parser.add_argument("--once", action="store_true", help="Process one pass and exit")
    parser.add_argument("--retry-failed", action="store_true", help="Requeue rejected inputs")
    parser.add_argument("--stop-file", type=Path, help=argparse.SUPPRESS)
    parser.add_argument("--ready-file", type=Path, help=argparse.SUPPRESS)
    args = parser.parse_args()
    try:
        config = load_config(args.config)
        protocols = load_protocol_candidates(args.protocol)
        configure_logging(
            config.storage_root, filename="analysis.log", console=args.stop_file is None
        )
    except Exception as error:
        print(f"Analysis setup failed ({type(error).__name__})")
        return 2
    stopped = shutdown_event(args.stop_file)
    logger = logging.getLogger("mr_quality_monitor.worker")
    try:
        with open_worker(config.storage_root, protocols, config) as worker:
            if args.retry_failed:
                worker.retry_failed()
            selected = [f"{p.protocol.protocol_id}:{p.protocol.version}" for p in protocols]
            logger.info(
                "analysis_started protocol_candidates=%s",
                ", ".join(selected),
            )
            if args.ready_file is not None:
                args.ready_file.touch()
            while not stopped.is_set():
                failures = worker.run_once(
                    stopped, defer_new_study=config.single_active_study and not args.once
                )
                if args.once:
                    return int(bool(failures))
                stopped.wait(2)
    except Exception as error:
        logger.error("analysis_failed type=%s", type(error).__name__)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

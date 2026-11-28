"""Create a synthetic GUI demo in a new directory; never modify an existing store."""

import argparse
from pathlib import Path

import yaml
from mr_quality_monitor.gui.demo import seed_demo
from mr_quality_monitor.protocols.manager import load_protocol


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument(
        "--protocol", type=Path, default=Path("tests/fixtures/protocols/gui_demo.yaml")
    )
    args = parser.parse_args()
    seed_demo(args.root, load_protocol(args.protocol))
    config = args.root / "viewer_config.yaml"
    config.write_text(yaml.safe_dump({"storage_root": "."}), encoding="utf-8")
    print(f"Synthetic DEMO created. Viewer configuration: {config}")


if __name__ == "__main__":
    main()

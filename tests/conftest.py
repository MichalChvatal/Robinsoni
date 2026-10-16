import os

import pytest

from trialink.catalog import default_export_path


def _has_export() -> bool:
    try:
        return default_export_path().exists()
    except FileNotFoundError:
        return False


HAS_EXPORT = _has_export()
# Hackathon data is git-ignored; tests that need the real SONIS export are skipped without it.
requires_export = pytest.mark.skipif(not HAS_EXPORT, reason="lokální export SONIS není k dispozici")
requires_catalog = pytest.mark.skipif(not (HAS_EXPORT or (os.getenv("IRIS_HOST") and os.getenv("IRIS_PASSWORD"))),
                                      reason="není export SONIS ani připojení k IRIS")

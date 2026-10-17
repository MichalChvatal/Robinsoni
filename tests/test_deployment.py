"""Deployment contract for Streamlit Community Cloud (no secrets or network)."""
import hashlib
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def test_cloud_install_uses_project_dependencies():
    assert (ROOT / "requirements.txt").read_text(encoding="utf-8").strip() == "."
    project = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    dependencies = project["project"]["dependencies"]
    assert any(value.startswith("pypdfium2") for value in dependencies)
    assert any(value.startswith("pillow") for value in dependencies)
    assert "data/ocr/*" in project["tool"]["setuptools"]["package-data"]["trialink"]


def test_cloud_has_czech_tesseract_and_matching_upload_limit():
    packages = (ROOT / "packages.txt").read_text(encoding="utf-8").splitlines()
    assert "tesseract-ocr" in packages
    assert "tesseract-ocr-ces" in packages
    config = tomllib.loads((ROOT / ".streamlit" / "config.toml").read_text(encoding="utf-8"))
    assert config["server"]["maxUploadSize"] == 10
    secrets = tomllib.loads((ROOT / ".streamlit" / "secrets.toml.example").read_text(encoding="utf-8"))
    assert "TESSERACT_CMD" not in secrets
    assert "TESSDATA_PREFIX" not in secrets


def test_bundled_czech_model_matches_documented_checksum_and_license():
    directory = ROOT / "trialink" / "data" / "ocr"
    digest = hashlib.sha256((directory / "ces.traineddata").read_bytes()).hexdigest()
    assert f"SHA256: {digest}" in (directory / "NOTICE").read_text(encoding="utf-8")
    assert "Apache License" in (directory / "LICENSE").read_text(encoding="utf-8")

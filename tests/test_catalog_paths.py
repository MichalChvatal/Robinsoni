import pytest

from trialink import catalog


@pytest.fixture
def export_root(tmp_path, monkeypatch):
    monkeypatch.setattr(catalog, "REPO_ROOT", tmp_path)
    monkeypatch.delenv("TRIALINK_EXPORT", raising=False)
    return tmp_path


def _export(root, layout):
    directory = root.joinpath(*layout, "Export z NIS")
    directory.mkdir(parents=True)
    path = directory / "KH_otevreny_nabor_k_2026-09-29.csv"
    path.touch()
    return path


def test_finds_export_in_local_data(export_root):
    expected = _export(export_root, ["data"])
    assert catalog.default_export_path() == expected


def test_preserves_original_hackathon_layout(export_root):
    expected = _export(export_root, ["RAKATHON_2026", "RAKATHON_Data"])
    assert catalog.default_export_path() == expected


def test_local_data_takes_precedence(export_root):
    _export(export_root, ["RAKATHON_2026", "RAKATHON_Data"])
    expected = _export(export_root, ["data"])
    assert catalog.default_export_path() == expected


def test_explicit_export_takes_precedence(export_root, monkeypatch):
    _export(export_root, ["data"])
    expected = export_root / "configured.csv"
    monkeypatch.setenv("TRIALINK_EXPORT", str(expected))
    assert catalog.default_export_path() == expected


def test_missing_export_raises_actionable_error(export_root):
    with pytest.raises(FileNotFoundError, match="TRIALINK_EXPORT"):
        catalog.default_export_path()


def test_ignores_other_csv_files_and_directories(export_root):
    directory = export_root / "data" / "Export z NIS"
    directory.mkdir(parents=True)
    (directory / "unrelated.csv").touch()
    (directory / "KH_otevreny_nabor_directory.csv").mkdir()
    with pytest.raises(FileNotFoundError):
        catalog.default_export_path()

import pytest
from mr_quality_monitor.config import ReceiverConfig, load_config
from pydantic import ValidationError


@pytest.mark.parametrize(
    "fields",
    [
        {"port": 0},
        {"port": 65536},
        {"ae_title": " "},
        {"allowed_calling_aet": "A" * 17},
        {"ae_title": "WITH\\SLASH"},
        {"unexpected": True},
        {"allowed_peer_ip": "not-an-ip"},
    ],
)
def test_invalid_configuration(tmp_path, fields):
    with pytest.raises(ValidationError):
        ReceiverConfig(storage_root=tmp_path, **fields)


def test_relative_storage_is_relative_to_config_file(tmp_path):
    path = tmp_path / "receiver.yaml"
    path.write_text("storage_root: ./store\n", encoding="utf-8")
    assert load_config(path).storage_root == (tmp_path / "store").resolve()

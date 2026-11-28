"""Strict, explicit receiver configuration without clinical defaults."""

from ipaddress import IPv4Address, IPv6Address
from pathlib import Path

import yaml
from pydantic import BaseModel, ConfigDict, Field, field_validator


class ReceiverConfig(BaseModel):
    """One receiver and one permitted MRI peer, bound to a literal local address."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    host: IPv4Address | IPv6Address = IPv4Address("127.0.0.1")
    port: int = Field(default=11112, ge=1, le=65535)
    ae_title: str = "MR_QUALITY"
    allowed_calling_aet: str = "MR_TEST"
    allowed_peer_ip: IPv4Address | IPv6Address = IPv4Address("127.0.0.1")
    storage_root: Path
    minimum_free_bytes: int = Field(default=104857600, ge=0)
    maximum_associations: int = Field(default=4, ge=1, le=32)
    network_timeout_seconds: int = Field(default=30, ge=1, le=300)

    @field_validator("ae_title", "allowed_calling_aet")
    @classmethod
    def valid_ae_title(cls, value: str) -> str:
        """DICOM AE values must be nonempty printable ASCII, at most 16 characters."""
        if not 1 <= len(value) <= 16 or value != value.strip():
            raise ValueError("AE title must have 1..16 characters without edge spaces")
        if any(ord(char) < 32 or ord(char) > 126 or char == "\\" for char in value):
            raise ValueError("Invalid AE title characters")
        return value


def load_config(path: Path) -> ReceiverConfig:
    """Load YAML, resolving the storage path against the configuration directory."""
    path = path.resolve()
    with path.open(encoding="utf-8") as stream:
        config = ReceiverConfig.model_validate(yaml.safe_load(stream))
    root = config.storage_root
    if not root.is_absolute():
        root = path.parent / root
    return config.model_copy(update={"storage_root": root.resolve()})

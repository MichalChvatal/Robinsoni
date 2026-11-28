"""Strict, explicit receiver configuration without clinical defaults."""

import re
from ipaddress import IPv4Address, IPv6Address
from pathlib import Path

import yaml
from pydantic import BaseModel, ConfigDict, Field, field_validator

TAG_RE = re.compile(r"^[0-9A-Fa-f]{4},[0-9A-Fa-f]{4}$")


class PacsConfig(BaseModel):
    """Target PACS C-STORE SCP configuration for automated/approved forwarding."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    enabled: bool = False
    host: str = "127.0.0.1"
    port: int = Field(default=11113, ge=1, le=65535)
    called_aet: str = "PACS_SCP"
    calling_aet: str = "MR_BUDDY"
    auto_forward_clean: bool = True
    require_manual_approval_on_issues: bool = True

    @field_validator("called_aet", "calling_aet")
    @classmethod
    def valid_pacs_ae_title(cls, value: str) -> str:
        if not 1 <= len(value) <= 16 or value != value.strip():
            raise ValueError("AE title must have 1..16 characters without edge spaces")
        if any(ord(char) < 32 or ord(char) > 126 or char == "\\" for char in value):
            raise ValueError("Invalid AE title characters")
        return value


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
    b_value_tag_order: tuple[str, ...] = ("0019,100C", "0021,1105")
    pacs: PacsConfig = PacsConfig()
    protocol_path: Path | None = None

    @field_validator("ae_title", "allowed_calling_aet")
    @classmethod
    def valid_ae_title(cls, value: str) -> str:
        """DICOM AE values must be nonempty printable ASCII, at most 16 characters."""
        if not 1 <= len(value) <= 16 or value != value.strip():
            raise ValueError("AE title must have 1..16 characters without edge spaces")
        if any(ord(char) < 32 or ord(char) > 126 or char == "\\" for char in value):
            raise ValueError("Invalid AE title characters")
        return value

    @field_validator("b_value_tag_order")
    @classmethod
    def valid_b_value_tags(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        if not value:
            raise ValueError("At least one b-value tag must be configured")
        normalized = []
        for item in value:
            cleaned = item.strip().upper()
            if not TAG_RE.match(cleaned):
                raise ValueError(f"Invalid DICOM tag format: {item}")
            normalized.append(cleaned)
        if len(set(normalized)) != len(normalized):
            raise ValueError("Duplicate b-value tags in configuration")
        return tuple(normalized)


def load_config(path: Path) -> ReceiverConfig:
    """Load YAML, resolving paths against the configuration directory."""
    path = path.resolve()
    with path.open(encoding="utf-8") as stream:
        config = ReceiverConfig.model_validate(yaml.safe_load(stream))
    updates: dict = {}
    root = config.storage_root
    if not root.is_absolute():
        root = path.parent / root
    updates["storage_root"] = root.resolve()
    if config.protocol_path is not None and not config.protocol_path.is_absolute():
        updates["protocol_path"] = (path.parent / config.protocol_path).resolve()
    return config.model_copy(update=updates)

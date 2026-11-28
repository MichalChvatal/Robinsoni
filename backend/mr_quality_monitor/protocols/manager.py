"""Safe YAML/JSON loading and immutable, examination-bound protocol snapshots."""

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path

import yaml

from .models import Protocol


def unique_mapping(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("Duplicate configuration key")
        result[key] = value
    return result


class UniqueSafeLoader(yaml.SafeLoader):
    pass


def yaml_mapping(loader, node):
    loader.flatten_mapping(node)
    return unique_mapping(loader.construct_pairs(node, deep=True))


UniqueSafeLoader.add_constructor(yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG, yaml_mapping)


@dataclass(frozen=True)
class ProtocolSnapshot:
    """The stored canonical JSON string is immutable, including its nested contents."""

    content: str
    sha256: str

    def __post_init__(self):
        if hashlib.sha256(self.content.encode()).hexdigest() != self.sha256:
            raise ValueError("Protocol snapshot checksum mismatch")
        Protocol.model_validate_json(self.content)

    @classmethod
    def create(cls, protocol: Protocol) -> "ProtocolSnapshot":
        data = protocol.model_dump(mode="json")
        if protocol.prostate_checks is None:
            del data["prostate_checks"]
        content = json.dumps(data, sort_keys=True, separators=(",", ":"))
        return cls(content, hashlib.sha256(content.encode()).hexdigest())

    @property
    def protocol(self) -> Protocol:
        return Protocol.model_validate_json(self.content)


def load_protocol(path: Path) -> ProtocolSnapshot:
    """Load at most 1 MiB; reject unsafe YAML tags and duplicate YAML/JSON keys."""
    with path.open("rb") as stream:
        raw = stream.read(1024 * 1024 + 1)
    if len(raw) > 1024 * 1024:
        raise ValueError("Protocol file exceeds 1 MiB")
    text = raw.decode("utf-8-sig")
    if path.suffix.lower() == ".json":
        data = json.loads(text, object_pairs_hook=unique_mapping)
    elif path.suffix.lower() in {".yaml", ".yml"}:
        data = yaml.load(text, Loader=UniqueSafeLoader)
    else:
        raise ValueError("Protocol must use YAML or JSON")
    return ProtocolSnapshot.create(Protocol.model_validate(data))


class ProtocolManager:
    """In-process protocol selection; persistent examination binding follows in stage 5."""

    def __init__(self):
        self._versions: dict[tuple[str, str], ProtocolSnapshot] = {}
        self._active: dict[str, ProtocolSnapshot] = {}
        self._selected: ProtocolSnapshot | None = None

    def register(self, snapshot: ProtocolSnapshot) -> None:
        protocol = snapshot.protocol
        key = (protocol.protocol_id, protocol.version)
        old = self._versions.get(key)
        if old is not None and old.sha256 != snapshot.sha256:
            raise ValueError("An existing protocol version cannot change content")
        self._versions[key] = snapshot

    def select(self, protocol_id: str, version: str) -> None:
        snapshot = self._versions[(protocol_id, version)]
        if self._active and self._selected != snapshot:
            raise RuntimeError("Cannot change selected protocol during an active examination")
        self._selected = snapshot

    def begin(self, study_uid: str) -> ProtocolSnapshot:
        if not study_uid.strip():
            raise ValueError("Study identity is required")
        if study_uid in self._active:
            return self._active[study_uid]
        if self._selected is None:
            raise RuntimeError("No active protocol selected")
        self._active[study_uid] = self._selected
        return self._selected

    def end(self, study_uid: str) -> None:
        del self._active[study_uid]

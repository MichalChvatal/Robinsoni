"""Typed metadata values with explicit units and provenance; no clinical limits."""

import math
from dataclasses import asdict, dataclass
from typing import Literal

State = Literal["known", "missing", "invalid", "conflicting"]
Scalar = str | float | int
Value = Scalar | tuple[Scalar, ...] | None


@dataclass(frozen=True)
class FieldSpec:
    kind: Literal["text", "float", "int", "uid"]
    unit: str | None = None
    count: int | None = 1  # None means a variable-length tuple, including one item.


@dataclass(frozen=True)
class Evidence:
    path: str
    tag: str
    raw: tuple[str, ...]
    unit: str | None = None
    parser: str = "standard/1"
    invalid: bool = False


@dataclass(frozen=True)
class MetadataValue:
    value: Value
    unit: str | None
    state: State
    sources: tuple[Evidence, ...] = ()
    reason: str | None = None


def normalize(spec: FieldSpec, sources: tuple[Evidence, ...]) -> MetadataValue:
    """Select the highest-priority source while retaining conflicts and raw values.

    Invalid populated evidence never silently falls back to another source. Empty
    values may be supplemented by a lower-priority source. No missing value is zero.
    """
    if any(source.invalid for source in sources):
        return MetadataValue(None, spec.unit, "invalid", sources, "unsupported_representation")
    populated = tuple(source for source in sources if source.raw and any(source.raw))
    if not populated:
        return MetadataValue(None, spec.unit, "missing", sources, "not_available")
    parsed: list[Value] = []
    try:
        for source in populated:
            if spec.count is not None and len(source.raw) != spec.count:
                raise ValueError("cardinality")
            values: list[Scalar] = []
            for raw in source.raw:
                if not raw.strip():
                    raise ValueError("empty_component")
                if spec.kind in {"float", "int"}:
                    number = float(raw)
                    if not math.isfinite(number):
                        raise ValueError("nonfinite")
                    scale = 1.0
                    if source.unit != spec.unit:
                        conversions = {("s", "ms"): 1000.0, ("cm", "mm"): 10.0}
                        scale = conversions[(source.unit, spec.unit)]
                    number *= scale
                    if not math.isfinite(number):
                        raise ValueError("nonfinite")
                    if spec.kind == "int":
                        if not number.is_integer():
                            raise ValueError("not_integer")
                        values.append(int(number))
                    else:
                        values.append(number)
                elif spec.kind == "uid":
                    # Validate syntax without constructing pydicom UID warnings with PHI.
                    parts = raw.split(".")
                    if len(raw) > 64 or not all(
                        part.isascii()
                        and part.isdigit()
                        and (part == "0" or not part.startswith("0"))
                        for part in parts
                    ):
                        raise ValueError("uid")
                    values.append(raw)
                else:
                    values.append(raw)
            parsed.append(values[0] if spec.count == 1 else tuple(values))
    except (ValueError, TypeError, KeyError, OverflowError):
        return MetadataValue(None, spec.unit, "invalid", sources, "invalid_value_or_unit")
    state = "known" if all(value == parsed[0] for value in parsed) else "conflicting"
    return MetadataValue(
        parsed[0],
        spec.unit,
        state,
        sources,
        "source_disagreement" if state == "conflicting" else None,
    )


@dataclass(frozen=True)
class MetadataRecord:
    fields: dict[str, MetadataValue]
    frame_index: int | None = None  # zero-based, None for classic single-frame objects
    parser_version: str = "metadata/1"
    vendor_parser: str = "generic/1"

    def known(self, name: str) -> Value:
        value = self.fields.get(name)
        return value.value if value is not None and value.state == "known" else None

    def to_dict(self) -> dict:
        """JSON-compatible snapshot; never use this return value in operational logs."""
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict) -> "MetadataRecord":
        """Restore our version-1 JSON snapshot, including tuple-valued metadata."""
        if data.get("parser_version") != "metadata/1":
            raise ValueError("Unsupported metadata snapshot version")
        fields = {}
        for name, raw in data["fields"].items():
            value = raw["value"]
            sources = tuple(
                Evidence(**{**source, "raw": tuple(source["raw"])}) for source in raw["sources"]
            )
            fields[name] = MetadataValue(
                tuple(value) if isinstance(value, list) else value,
                raw["unit"],
                raw["state"],
                sources,
                raw["reason"],
            )
        return cls(fields, data["frame_index"], data["parser_version"], data["vendor_parser"])

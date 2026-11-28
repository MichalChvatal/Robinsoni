"""Extension points for versioned, explicitly configured private metadata."""

import hashlib
import json
from dataclasses import asdict, dataclass

from pydicom.dataset import Dataset
from pydicom.multival import MultiValue

from ..analysis.metadata_normalizer import Evidence


def raw_strings(value) -> tuple[str, ...]:
    """Retain original decimal spelling when supplied by pydicom."""
    if value is None:
        return ()
    if isinstance(value, bytes):
        # Binary private structures need a dedicated, verified decoder.
        raise ValueError("Binary vendor fields require a dedicated decoder")
    # PersonName is iterable over characters but remains one DICOM scalar value.
    values = value if isinstance(value, (list, tuple, MultiValue)) else (value,)
    return tuple(
        original
        if isinstance(original := getattr(item, "original_string", None), str)
        else str(item)
        for item in values
    )


@dataclass(frozen=True)
class PrivateBinding:
    keyword: str
    group: int
    creator: str
    offset: int
    model: str
    software_version: str
    unit: str | None = None

    def __post_init__(self) -> None:
        if (
            not 0 < self.group <= 0xFFFF
            or self.group % 2 != 1
            or not 0 <= self.offset <= 0xFF
            or not self.creator.strip()
            or not self.model.strip()
            or not self.software_version.strip()
        ):
            raise ValueError("Private binding requires creator, model, version and valid tag")


class VendorAdapter:
    """No hardcoded private tags; bindings must match exact model and software version."""

    name = "generic"
    version = "1"

    def __init__(self, bindings: tuple[PrivateBinding, ...] = ()):
        self.bindings = bindings

    @property
    def identifier(self) -> str:
        base = f"{self.name}/{self.version}"
        if not self.bindings:
            return base
        snapshot = json.dumps([asdict(binding) for binding in self.bindings], sort_keys=True)
        return f"{base}+{hashlib.sha256(snapshot.encode()).hexdigest()}"

    def extract(self, dataset: Dataset) -> dict[str, tuple[Evidence, ...]]:
        result: dict[str, tuple[Evidence, ...]] = {}
        versions = raw_strings(getattr(dataset, "SoftwareVersions", None))
        model = str(getattr(dataset, "ManufacturerModelName", ""))
        for binding in self.bindings:
            if model != binding.model or binding.software_version not in versions:
                continue
            try:
                element = dataset.get_private_item(binding.group, binding.offset, binding.creator)
            except KeyError:
                continue
            invalid = False
            try:
                raw = raw_strings(element.value)
            except ValueError:
                raw, invalid = (), True
            evidence = Evidence(
                path=f"private[{binding.creator}].{int(element.tag):08X}",
                tag=f"{int(element.tag):08X}",
                raw=raw,
                unit=binding.unit,
                parser=self.identifier,
                invalid=invalid,
            )
            result[binding.keyword] = (*result.get(binding.keyword, ()), evidence)
        return result


def adapter_for(manufacturer: str) -> VendorAdapter:
    # Exact normalized aliases avoid classifying arbitrary substrings as a vendor.
    from .canon import CanonAdapter
    from .ge import GEAdapter
    from .philips import PhilipsAdapter
    from .siemens import SiemensAdapter

    aliases = {
        "SIEMENS": SiemensAdapter,
        "SIEMENS HEALTHINEERS": SiemensAdapter,
        "PHILIPS": PhilipsAdapter,
        "PHILIPS MEDICAL SYSTEMS": PhilipsAdapter,
        "GE": GEAdapter,
        "GE MEDICAL SYSTEMS": GEAdapter,
        "CANON": CanonAdapter,
        "CANON MEDICAL SYSTEMS": CanonAdapter,
        "CANON MEDICAL SYSTEMS CORPORATION": CanonAdapter,
    }
    return aliases.get(manufacturer.strip().upper(), VendorAdapter)()

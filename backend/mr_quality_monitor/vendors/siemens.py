import hashlib
import json

from pydicom.dataset import Dataset

from ..analysis.metadata_normalizer import Evidence
from .base import PrivateBinding, VendorAdapter, raw_strings

SIEMENS_B_VALUE_CREATORS: dict[str, tuple[int, int, tuple[str, ...]]] = {
    "0019,100C": (0x0019, 0x0C, ("SIEMENS MR HEADER",)),
    "0021,1105": (0x0021, 0x05, ("SIEMENS MR SDI 02", "SIEMENS MR SDS 01")),
}


class SiemensAdapter(VendorAdapter):
    """Siemens extension point with configurable private b-value tag priority."""

    name = "siemens"

    def __init__(
        self,
        bindings: tuple[PrivateBinding, ...] = (),
        *,
        b_value_tags: tuple[str, ...] = (),
    ):
        super().__init__(bindings)
        self.b_value_tags = tuple(tag.strip().upper() for tag in b_value_tags)

    @property
    def identifier(self) -> str:
        base = super().identifier
        if not self.b_value_tags:
            return base
        digest = hashlib.sha256(json.dumps(self.b_value_tags).encode()).hexdigest()
        return f"{base}+bval:{digest[:12]}"

    def extract(self, dataset: Dataset) -> dict[str, tuple[Evidence, ...]]:
        result = super().extract(dataset)
        if self.b_value_tags:
            for tag_key in self.b_value_tags:
                spec = SIEMENS_B_VALUE_CREATORS.get(tag_key)
                if spec is None:
                    continue
                group, offset, creators = spec
                element = None
                used_creator = None
                for creator in creators:
                    try:
                        element = dataset.get_private_item(group, offset, creator)
                        used_creator = creator
                        break
                    except KeyError:
                        continue
                if element is None:
                    continue
                invalid = False
                try:
                    raw = raw_strings(element.value)
                except ValueError:
                    raw, invalid = (), True
                if not raw and not invalid:
                    continue
                evidence = Evidence(
                    path=f"private[{used_creator}].{int(element.tag):08X}",
                    tag=f"{int(element.tag):08X}",
                    raw=raw,
                    unit="s/mm2",
                    parser=self.identifier,
                    invalid=invalid,
                )
                result["DiffusionBValue"] = (*result.get("DiffusionBValue", ()), evidence)
                # Use the highest-priority configured tag that is populated as primary,
                # falling back to subsequent tags only when absent.
                break
        return result

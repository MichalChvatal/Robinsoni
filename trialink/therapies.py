"""Drug -> therapy class dictionary. Classes are what trial rules refer to."""
from __future__ import annotations

import unicodedata

from .model import TherapyEpisode

ANY_ADVANCED = "jakakoli systemova lecba pro pokrocile onemocneni"

# Each class may imply broader classes.
CLASS_PARENTS: dict[str, set[str]] = {
    "trastuzumab deruxtecan": {"anti-HER2 ADC", "topoizomeraza ADC"},
    "T-DM1": {"anti-HER2 ADC"},
    "anti-HER2 ADC": {"anti-HER2", "ADC"},
    "anti-HER2 TKI": {"anti-HER2", "TKI"},
    "TROP2 ADC": {"topoizomeraza ADC", "ADC"},
    "topoizomeraza ADC": {"ADC"},
    "anti-PD-1/PD-L1": {"imunoterapie"},
    "KRAS G12C inhibitor": {"TKI"},
    "docetaxel": {"taxan"},
}

DRUGS: dict[str, set[str]] = {
    "folfox": {"platina", "fluoropyrimidin"},
    "capox": {"platina", "fluoropyrimidin"},
    "xelox": {"platina", "fluoropyrimidin"},
    "folfiri": {"fluoropyrimidin", "irinotekan"},
    "folfoxiri": {"platina", "fluoropyrimidin", "irinotekan"},
    "oxaliplatina": {"platina"},
    "cisplatina": {"platina"},
    "karboplatina": {"platina"},
    "5-fu": {"fluoropyrimidin"},
    "fluorouracil": {"fluoropyrimidin"},
    "kapecitabin": {"fluoropyrimidin"},
    "irinotekan": {"irinotekan"},
    "nivolumab": {"anti-PD-1/PD-L1"},
    "pembrolizumab": {"anti-PD-1/PD-L1"},
    "atezolizumab": {"anti-PD-1/PD-L1"},
    "durvalumab": {"anti-PD-1/PD-L1"},
    "tislelizumab": {"anti-PD-1/PD-L1"},
    "paklitaxel": {"taxan"},
    "paclitaxel": {"taxan"},
    "docetaxel": {"docetaxel"},
    "kabazitaxel": {"taxan"},
    "doxorubicin": {"antracyklin"},
    "epirubicin": {"antracyklin"},
    "ac": {"antracyklin"},
    "ddac": {"antracyklin"},
    "trastuzumab": {"anti-HER2"},
    "pertuzumab": {"anti-HER2"},
    "phesgo": {"anti-HER2"},
    "kadcyla": {"T-DM1"},
    "trastuzumab emtansin": {"T-DM1"},
    "t-dm1": {"T-DM1"},
    "enhertu": {"trastuzumab deruxtecan"},
    "trastuzumab deruxtecan": {"trastuzumab deruxtecan"},
    "t-dxd": {"trastuzumab deruxtecan"},
    "tukatinib": {"anti-HER2 TKI"},
    "tucatinib": {"anti-HER2 TKI"},
    "lapatinib": {"anti-HER2 TKI"},
    "neratinib": {"anti-HER2 TKI"},
    "sacituzumab govitekan": {"TROP2 ADC"},
    "sacituzumab tirumotekan": {"TROP2 ADC"},
    "datopotamab deruxtekan": {"TROP2 ADC"},
    "letrozol": {"endokrinni terapie"},
    "anastrozol": {"endokrinni terapie"},
    "exemestan": {"endokrinni terapie"},
    "tamoxifen": {"endokrinni terapie"},
    "fulvestrant": {"endokrinni terapie"},
    "palbociklib": {"CDK4/6 inhibitor"},
    "ribociklib": {"CDK4/6 inhibitor"},
    "abemaciklib": {"CDK4/6 inhibitor"},
    "olaparib": {"PARP inhibitor"},
    "talazoparib": {"PARP inhibitor"},
    "bevacizumab": {"anti-VEGF"},
    "aflibercept": {"anti-VEGF"},
    "cetuximab": {"anti-EGFR"},
    "panitumumab": {"anti-EGFR"},
    "sotorasib": {"KRAS G12C inhibitor"},
    "adagrasib": {"KRAS G12C inhibitor"},
    "abirateron": {"NHA"},
    "enzalutamid": {"NHA"},
    "apalutamid": {"NHA"},
    "darolutamid": {"NHA"},
    "lomustin": {"nitrourea"},
    "sunitinib": {"VEGF-TKI"},
    "pazopanib": {"VEGF-TKI"},
    "kabozantinib": {"VEGF-TKI"},
    "axitinib": {"VEGF-TKI"},
    "lenvatinib": {"VEGF-TKI"},
    "tivozanib": {"VEGF-TKI"},
}

ALL_CLASSES = sorted(
    {c for cs in DRUGS.values() for c in cs}
    | set(CLASS_PARENTS)
    | {p for ps in CLASS_PARENTS.values() for p in ps}
    | {ANY_ADVANCED}
)


def _fold(text: str) -> str:
    return "".join(ch for ch in unicodedata.normalize("NFKD", text.lower()) if not unicodedata.combining(ch)).strip()


_FOLDED_DRUGS = {_fold(k): v for k, v in DRUGS.items()}
_FOLDED_CLASSES = {_fold(c): c for c in ALL_CLASSES}


def expand(classes: set[str]) -> set[str]:
    out = set(classes)
    stack = list(classes)
    while stack:
        for parent in CLASS_PARENTS.get(stack.pop(), ()):
            if parent not in out:
                out.add(parent)
                stack.append(parent)
    return out


def classes_of(name: str) -> set[str]:
    """Classes for a drug/regimen name or a class name. Unknown names map to an empty set."""
    key = _fold(name)
    if key in _FOLDED_CLASSES:
        return expand({_FOLDED_CLASSES[key]})
    found: set[str] = set()
    for part in key.replace("+", " + ").split(" + "):
        part = part.strip()
        if part in _FOLDED_DRUGS:
            found |= _FOLDED_DRUGS[part]
    if not found and key in _FOLDED_DRUGS:
        found = set(_FOLDED_DRUGS[key])
    return expand(found)


def patient_classes(therapies: list[TherapyEpisode], palliative_only: bool = False) -> set[str]:
    out: set[str] = set()
    for t in therapies:
        if palliative_only and t.setting != "paliativni":
            continue
        out |= classes_of(t.name)
        if t.setting == "paliativni":
            out.add(ANY_ADVANCED)
    return out


def unknown_names(therapies: list[TherapyEpisode]) -> list[str]:
    return [t.name for t in therapies if not classes_of(t.name)]

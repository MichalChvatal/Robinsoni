"""Structured profiles of the supplied anonymized Case 1-5 (expected reviewed extraction). Test/demo fixtures only."""
from __future__ import annotations

from .model import BiomarkerResult as B
from .model import PatientProfile, TherapyEpisode as T


def case1() -> PatientProfile:
    p = PatientProfile(icd10="C186", histology="adenokarcinom", disease_extent="metastaticke", setting="paliativni",
                       prior_palliative_lines=0, therapies=[], ecog=0, age=54, sex="M", can_swallow=True)
    p.set_biomarker(B("KRAS", "pozitivni", "G13D"))
    p.set_biomarker(B("BRAF", "negativni"))
    p.set_biomarker(B("dMMR/MSI-H", "negativni"))
    p.conflicts = ["Diagnostický souhrn uvádí 'pT0' u uzlin – pravděpodobně pN0 (0/27)."]
    return p


def case2() -> PatientProfile:
    p = PatientProfile(icd10="C154", histology="skvamozni", disease_extent="metastaticke", setting="paliativni",
                       prior_palliative_lines=1, therapies=[T("FOLFOX + nivolumab", "paliativni")],
                       ecog=1, age=68, sex="M", can_swallow=False)
    p.set_biomarker(B("PD-L1 TPS", "pozitivni", values=[10]))
    p.conflicts = ["Histologie popisuje skvamózní karcinom jícnu, ale klasifikace uvádí C186 / 8140 (adenokarcinom tračníku) – zřejmě zkopírovaný text."]
    return p


def case3() -> PatientProfile:
    p = PatientProfile(icd10="C341", histology="adenokarcinom", disease_extent="metastaticke", setting="paliativni",
                       prior_palliative_lines=0, therapies=[], ecog=1, age=62, sex="F", can_swallow=True)
    p.set_biomarker(B("KRAS", "pozitivni", "p.(Gly12Cys)", method="NGS"))
    p.set_biomarker(B("STK11", "pozitivni", "D53Tfs*11", method="NGS"))
    for m in ("EGFR", "ALK", "ROS1", "RET", "BRAF", "MET"):
        p.set_biomarker(B(m, "negativni", method="NGS"))
    p.set_biomarker(B("PD-L1 TPS", "pozitivni", values=[100]))
    p.conflicts = ["Text uvádí cN0 i M1b, ale popisuje i jaterní metastázy (M1c?)."]
    return p


def case4() -> PatientProfile:
    p = PatientProfile(icd10="C509", histology="duktalni_nst", disease_extent="metastaticke", setting="paliativni",
                       prior_palliative_lines=1, her2_status="pozitivni", ecog=0, age=49, sex="F", can_swallow=True,
                       therapies=[T("ddAC", "neoadjuvance"), T("paklitaxel + Phesgo", "neoadjuvance"), T("Kadcyla", "adjuvance"),
                                  T("letrozol", "adjuvance"), T("Enhertu", "paliativni")])
    p.set_biomarker(B("ER", "pozitivni", values=[80, 100]))
    p.set_biomarker(B("PR", "pozitivni", values=[90, 100]))
    p.conflicts = ["Neoadjuvance uvedena od 2/2026, ale operace 2/2024 – nesoulad dat.",
                   "HER2 v reziduu invazivní složky 1+, v uzlinové metastáze 3+/FISH+."]
    return p


def case5() -> PatientProfile:
    p = PatientProfile(icd10="C504", histology="duktalni_nst", disease_extent="lokalizovane_resekabilni", setting="neoadjuvance",
                       prior_palliative_lines=0, therapies=[], her2_status="0", ecog=0, age=49, sex="F", can_swallow=True)
    p.set_biomarker(B("ER", "negativni", values=[1, 0]))
    p.set_biomarker(B("PR", "negativni", values=[0]))
    p.set_biomarker(B("PD-L1 CPS", "pozitivni", values=[15]))
    p.set_biomarker(B("BRCA1", "nevysetreno"))
    p.set_biomarker(B("BRCA2", "nevysetreno"))
    p.conflicts = ["ER uvedeno jako 1 % v histologii a 0 % v souhrnu."]
    return p


CASES = {
    "Case 1 – CRC, KRAS G13D, metastatický": case1,
    "Case 2 – skvamózní ca jícnu, po 1. linii": case2,
    "Case 3 – NSCLC adenokarcinom, KRAS G12C": case3,
    "Case 4 – HER2+ ca prsu, po T-DM1 a T-DXd": case4,
    "Case 5 – TNBC, plánovaná neoadjuvance": case5,
}

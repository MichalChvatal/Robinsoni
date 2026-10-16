"""Deterministic referral message + simulated send. Nothing is stored or transmitted."""
from __future__ import annotations

from dataclasses import dataclass

from .catalog import Contact
from .dictionaries import format_morpho
from .matching import STATUS_LABELS, CohortResult
from .model import EXTENTS, HER2_STATUSES, HISTOLOGIES, SETTINGS, PatientProfile


@dataclass
class Referral:
    recipients: list[Contact]
    subject: str
    body: str
    blockers: list[str]


def _summary(p: PatientProfile) -> list[str]:
    lines = []
    if p.icd10:
        lines.append(f"Diagnóza (MKN-10): {p.icd10}")
    if p.morphology:
        lines.append(f"Morfologie (ICD-O-3): {format_morpho(p.morphology)}")
    if p.histology:
        lines.append(f"Histologie: {HISTOLOGIES.get(p.histology, p.histology)}")
    if p.disease_extent:
        lines.append(f"Rozsah onemocnění: {EXTENTS[p.disease_extent]}")
    if p.setting:
        lines.append(f"Plánovaná léčba: {SETTINGS[p.setting]}")
    if p.prior_palliative_lines is not None:
        lines.append(f"Předchozí linie paliativní léčby: {p.prior_palliative_lines}")
    if p.her2_status:
        lines.append(f"HER2: {HER2_STATUSES[p.her2_status]}")
    for b in p.biomarkers.values():
        detail = " ".join(x for x in [b.variant or "", ", ".join(f"{v:g}" for v in b.values)] if x)
        lines.append(f"{b.marker}: {b.status}{' – ' + detail if detail else ''}{' (ROZPOR)' if b.conflict else ''}")
    if p.therapies:
        lines.append("Předchozí léčba: " + "; ".join(f"{t.name} ({t.setting or 'režim neuveden'})" for t in p.therapies))
    if p.ecog is not None:
        lines.append(f"ECOG: {p.ecog}")
    if p.age is not None:
        lines.append(f"Věk: {p.age}")
    return lines


def build_referral(result: CohortResult, p: PatientProfile, sender: str = "") -> Referral:
    s = result.study
    recipients = [c for c in s.contacts if c.email and c.name]
    body = [
        "Dobrý den,",
        "",
        f"prosím o posouzení vhodnosti pacienta pro klinickou studii {s.acronym} (ID {s.study_id}, protokol {s.protocol}).",
        f"Kohorta: {result.cohort['label']}",
        f"Předběžné hodnocení TriaLink: {STATUS_LABELS[result.status]}",
        "",
        "Klinický souhrn (bez identifikačních údajů):",
        *[f"  • {line}" for line in _summary(p)],
        "",
        "Splněná kritéria:",
        *[f"  ✓ {r.reason}" for r in result.satisfied],
        "",
        "K ověření:",
        *([f"  ? {r.criterion.get('value') if r.criterion['field'] == 'manual' else r.reason}" for r in result.open_items] or ["  – nic"]),
    ]
    if p.conflicts:
        body += ["", "Rozpory ve zdrojové dokumentaci:", *[f"  ! {c}" for c in p.conflicts]]
    body += [
        "",
        "Identifikaci pacienta sdělím po vašem kontaktu.",
        "Hodnocení je předběžné a nenahrazuje posouzení zkoušejícím podle protokolu.",
        "",
        f"S pozdravem{(chr(10) + sender) if sender else ''}",
    ]
    return Referral(recipients, f"[TriaLink] Referral do studie {s.acronym} ({s.study_id})", "\n".join(body), s.referral_blockers)


def simulate_send(referral: Referral) -> str:
    """No SMTP, no storage, no logging. Returns a confirmation text for the UI only."""
    if referral.blockers:
        raise ValueError("Referral nelze odeslat: " + "; ".join(referral.blockers))
    names = ", ".join(f"{c.role} {c.name} <{c.email}>" for c in referral.recipients)
    return f"SIMULACE: e-mail by byl odeslán na {names}. Žádná zpráva nebyla skutečně odeslána ani uložena."

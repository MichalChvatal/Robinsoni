"""Deterministic 4-state criterion evaluation. Runs in application RAM; patient data never reaches SQL."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from . import therapies as th
from .model import BIOMARKER_STATUSES, EXTENTS, HER2_STATUSES, HISTOLOGIES, SETTINGS, PatientProfile, normalize_icd, normalize_variant

SAT, NOT, UNK, CONF = "splneno", "nesplneno", "nezname", "rozpor"

STATUS_CANDIDATE, STATUS_VERIFY, STATUS_EXCLUDED = "kandidat", "k_overeni", "vyloucena"
STATUS_LABELS = {
    STATUS_CANDIDATE: "Kandidát – bez zjištěného rozporu",
    STATUS_VERIFY: "K ověření – chybí nebo je rozporný údaj",
    STATUS_EXCLUDED: "Vyloučena – nesplněné kritérium",
}
_TIER = {STATUS_CANDIDATE: 0, STATUS_VERIFY: 1, STATUS_EXCLUDED: 2}
NSCLC_HISTOLOGIES = {"adenokarcinom", "skvamozni", "nemalobunecny_nsclc", "velkobunecny"}


@dataclass
class CriterionResult:
    criterion: dict[str, Any]
    state: str
    reason: str


@dataclass
class CohortResult:
    study: Any
    cohort: dict[str, Any]
    results: list[CriterionResult]
    status: str = STATUS_VERIFY
    targeted: bool = False

    @property
    def failed(self) -> list[CriterionResult]:
        return [r for r in self.results if r.state == NOT]

    @property
    def open_items(self) -> list[CriterionResult]:
        return [r for r in self.results if r.state in (UNK, CONF)]

    @property
    def satisfied(self) -> list[CriterionResult]:
        return [r for r in self.results if r.state == SAT]

    def sort_key(self) -> tuple:
        key_hits = sum(1 for r in self.satisfied if r.criterion["field"] in ("biomarker", "biomarker_value", "her2_status", "diagnosis"))
        return (_TIER[self.status], not self.targeted, -key_hits, len(self.open_items), self.study.study_id)


def _labels(values, labels: dict[str, str]) -> str:
    return ", ".join(labels.get(v, str(v)) for v in values)


def _cmp(op: str, a: float, b: float) -> bool:
    return {"<": a < b, "<=": a <= b, ">=": a >= b, ">": a > b, "eq": a == b}[op]


def _condition(c: dict[str, Any], p: PatientProfile) -> tuple[str, str]:
    """Return (state, reason) of the criterion's condition itself, independent of inclusion/exclusion."""
    f, op, v = c["field"], c.get("op"), c.get("value")

    if f == "any_of":
        subs = [_condition(s, p) for s in v]
        states = [s for s, _ in subs]
        if SAT in states:
            return SAT, next(r for s, r in subs if s == SAT)
        if all(s == NOT for s in states):
            return NOT, "; ".join(r for _, r in subs)
        if CONF in states:
            return CONF, "; ".join(r for s, r in subs if s in (CONF, UNK))
        return UNK, "; ".join(r for s, r in subs if s == UNK)

    if f == "manual":
        return UNK, "Vyžaduje ruční posouzení podle protokolu"

    if f == "diagnosis":
        icd = normalize_icd(p.icd10)
        if not icd:
            return UNK, "Diagnóza (MKN-10) není zadána"
        prefixes = [normalize_icd(x) for x in v]
        ok = any(icd.startswith(x) for x in prefixes)
        return (SAT if ok else NOT), f"Diagnóza {icd} {'odpovídá' if ok else 'neodpovídá'} ({', '.join(v)})"

    if f == "histology":
        if not p.histology:
            return UNK, "Histologie není zadána"
        allowed = set(v)
        if "nemalobunecny_nsclc" in allowed:
            allowed |= NSCLC_HISTOLOGIES
        ok = p.histology in allowed
        return (SAT if ok else NOT), f"Histologie {HISTOLOGIES.get(p.histology, p.histology)} {'odpovídá' if ok else 'neodpovídá'} (požadováno: {_labels(v, HISTOLOGIES)})"

    if f == "disease_extent":
        if not p.disease_extent:
            return UNK, "Rozsah onemocnění není zadán"
        ok = p.disease_extent in v
        return (SAT if ok else NOT), f"Rozsah onemocnění: {EXTENTS.get(p.disease_extent)} (požadováno: {_labels(v, EXTENTS)})"

    if f == "setting":
        if not p.setting:
            return UNK, "Léčebná situace (neo/adjuvance, paliace) není zadána"
        ok = p.setting in v
        return (SAT if ok else NOT), f"Léčebná situace: {SETTINGS.get(p.setting)} (požadováno: {_labels(v, SETTINGS)})"

    if f == "prior_palliative_lines":
        if p.prior_palliative_lines is None:
            return UNK, "Počet předchozích linií paliativní léčby není znám"
        lo, hi = v
        ok = lo <= p.prior_palliative_lines <= hi
        return (SAT if ok else NOT), f"Předchozí paliativní linie: {p.prior_palliative_lines} (požadováno {lo}–{hi})"

    if f == "her2_status":
        if not p.her2_status:
            return UNK, "HER2 status není zadán"
        ok = p.her2_status in v
        return (SAT if ok else NOT), f"{HER2_STATUSES.get(p.her2_status)} (požadováno: {_labels(v, HER2_STATUSES)})"

    if f in ("biomarker", "biomarker_negative"):
        marker = v["marker"].upper()
        wanted = v.get("variant")
        wanted_list = [normalize_variant(x) for x in (wanted if isinstance(wanted, list) else [wanted] if wanted else [])]
        label = f"{v['marker']}{' ' + '/'.join(wanted_list) if wanted_list else ''}"
        b = p.biomarker(marker)
        if b is None or b.status in ("nevysetreno", "nelze"):
            return UNK, f"{label}: výsledek není k dispozici"
        if b.conflict:
            return CONF, f"{label}: rozporné výsledky ve zprávě"
        variant = normalize_variant(b.variant)
        if b.status == "pozitivni":
            if wanted_list and variant is None:
                return UNK, f"{v['marker']} pozitivní, ale varianta není uvedena (požadováno {label})"
            present = not wanted_list or variant in wanted_list
        else:
            present = False
        shown = f"{v['marker']} {BIOMARKER_STATUSES[b.status]}{' ' + variant if variant else ''}"
        if f == "biomarker":
            return (SAT if present else NOT), f"{shown} (požadováno {label})"
        return (NOT if present else SAT), f"{shown} (požadováno: bez {label})"

    if f == "biomarker_value":
        marker = v["marker"].upper()
        b = p.biomarker(marker)
        if b is None or not b.values:
            return UNK, f"{v['marker']}: hodnota není k dispozici"
        if op == "between":
            hits = [v["min"] <= x <= v["max"] for x in b.values]
            want = f"{v['min']}–{v['max']}"
        else:
            hits = [_cmp(op, x, v["threshold"]) for x in b.values]
            want = f"{op} {v['threshold']}"
        shown = ", ".join(f"{x:g}" for x in b.values)
        if all(hits):
            return SAT, f"{v['marker']} = {shown} (požadováno {want})"
        if not any(hits):
            return NOT, f"{v['marker']} = {shown} (požadováno {want})"
        return CONF, f"{v['marker']}: rozporné hodnoty {shown} (požadováno {want})"

    if f == "prior_therapy":
        if p.therapies is None:
            return UNK, "Předchozí léčba není známa"
        scope_pall = c.get("scope") == "paliativni"
        have = th.patient_classes(p.therapies, palliative_only=scope_pall)
        hit = sorted(have & set(v))
        where = " v paliativním režimu" if scope_pall else ""
        if hit:
            return SAT, f"Předchozí léčba{where} zahrnuje: {', '.join(hit)}"
        unknown = th.unknown_names(p.therapies)
        if unknown:
            return UNK, f"Nelze zařadit léky {', '.join(unknown)}; požadovaná skupina: {', '.join(v)}"
        return NOT, f"Předchozí léčba{where} nezahrnuje: {', '.join(v)}"

    if f == "ecog":
        if p.ecog is None:
            return UNK, "ECOG není zadán"
        ok = _cmp(op, p.ecog, v)
        return (SAT if ok else NOT), f"ECOG {p.ecog} (požadováno {op} {v})"

    if f == "age":
        if p.age is None:
            return UNK, "Věk není zadán"
        ok = _cmp(op, p.age, v)
        return (SAT if ok else NOT), f"Věk {p.age} (požadováno {op} {v})"

    if f == "sex":
        if not p.sex:
            return UNK, "Pohlaví není zadáno"
        ok = p.sex == v
        return (SAT if ok else NOT), f"Pohlaví {p.sex} (požadováno {v})"

    if f == "can_swallow":
        if p.can_swallow is None:
            return UNK, "Schopnost polykat není známa"
        ok = p.can_swallow == v
        return (SAT if ok else NOT), "Pacient polyká" if p.can_swallow else "Pacient nemůže polykat"

    return UNK, f"Neznámý typ kritéria: {f}"


def evaluate_criterion(c: dict[str, Any], p: PatientProfile) -> CriterionResult:
    state, reason = _condition(c, p)
    if c["kind"] == "exclusion":
        state = {SAT: NOT, NOT: SAT}.get(state, state)
        if state == NOT:
            reason = f"Vylučující: {reason}"
    return CriterionResult(c, state, reason)


def evaluate_cohort(study, cohort: dict[str, Any], p: PatientProfile) -> CohortResult:
    results = [evaluate_criterion(c, p) for c in cohort["criteria"]]
    res = CohortResult(study, cohort, results)
    if res.failed:
        res.status = STATUS_EXCLUDED
    elif res.open_items or not results:
        res.status = STATUS_VERIFY
    else:
        res.status = STATUS_CANDIDATE
    res.targeted = any(r.criterion["field"] == "diagnosis" and r.state == SAT for r in results)
    return res


def sex_caution(r: CohortResult, p: PatientProfile) -> str | None:
    """Advisory note only, the 4-state result is unchanged: male breast cancer vs. a study without a sex rule."""
    has_sex_rule = any(c["field"] == "sex" for c in r.cohort["criteria"])
    if p.sex == "M" and (normalize_icd(p.icd10) or "").startswith("C50") and r.targeted and not has_sex_rule:
        return "Studie u karcinomu prsu – ověřte u studijního týmu, zda přijímá i muže."
    return None


def match(studies, p: PatientProfile) -> list[CohortResult]:
    """Best cohort per study, sorted by tier then evidence."""
    best: dict[str, CohortResult] = {}
    for s in studies:
        for cohort in s.cohorts:
            r = evaluate_cohort(s, cohort, p)
            if s.study_id not in best or r.sort_key() < best[s.study_id].sort_key():
                best[s.study_id] = r
    return sorted(best.values(), key=CohortResult.sort_key)

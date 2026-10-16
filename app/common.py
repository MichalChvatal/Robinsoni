"""Shared Streamlit helpers. Patient data lives only in st.session_state (server RAM) and is cleared on reset."""
from __future__ import annotations

import os
import sys
import time
from pathlib import Path

import streamlit as st
from dotenv import load_dotenv

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from trialink.catalog import load_catalog  # noqa: E402
from trialink.dictionaries import ICD_NAMES, TopoMorphoIndex, diagnosis_label, format_morpho, histology_from_morpho, load_file  # noqa: E402
from trialink.matching import STATUS_CANDIDATE, STATUS_EXCLUDED, STATUS_LABELS, STATUS_VERIFY, CohortResult  # noqa: E402
from trialink.lay_terms import TOOLTIPS  # noqa: E402
from trialink.model import (  # noqa: E402
    BIOMARKER_STATUSES, EXTENTS, HER2_STATUSES, HISTOLOGIES, SETTINGS, BiomarkerResult, PatientProfile, TherapyEpisode, normalize_icd,
)

load_dotenv()


def _secrets_to_env() -> None:
    """On Streamlit Community Cloud configuration comes from st.secrets instead of .env."""
    try:
        items = dict(st.secrets)
    except Exception:
        return
    for key, value in items.items():
        if isinstance(value, (str, int, float)) and not os.getenv(key):
            os.environ[key] = str(value)


_secrets_to_env()

STATUS_ICONS = {STATUS_CANDIDATE: "🟢", STATUS_VERIFY: "🟡", STATUS_EXCLUDED: "🔴"}
STATE_ICONS = {"splneno": "✅", "nesplneno": "❌", "nezname": "❔", "rozpor": "⚠️"}
SESSION_TIMEOUT_S = 15 * 60


@st.cache_resource(show_spinner="Načítám studie…")
def get_catalog():
    """Reference data only (no patient data) – safe to cache across sessions."""
    source = "lokální export"
    if os.getenv("IRIS_HOST") and os.getenv("IRIS_PASSWORD"):
        try:
            from trialink import db
            conn = db.connect()
            try:
                studies = db.load_active_studies(conn)
            finally:
                conn.close()
            if studies:
                return studies, f"InterSystems IRIS ({os.getenv('IRIS_HOST')})", []
        except Exception as exc:
            source = f"lokální export (IRIS nedostupné: {type(exc).__name__})"
    try:
        studies, report, issues = load_catalog()
    except FileNotFoundError:
        st.error("Katalog studií není dostupný: nepodařilo se načíst studie z IRIS a lokální export SONIS chybí. "
                 "Na Streamlit Cloud nastavte v Settings → Secrets IRIS_HOST, IRIS_PORT, IRIS_NAMESPACE, IRIS_USER a IRIS_PASSWORD."
                 + (f" ({source})" if "IRIS" in source else ""))
        st.stop()
    return studies, source, issues


@st.cache_resource(show_spinner="Načítám číselník diagnóz a morfologií z databáze…", ttl=3600)
def get_topo_morpho() -> tuple[TopoMorphoIndex | None, str]:
    """Allowed topography x morphology pairs (cis_skupina) from IRIS TriaLink.TopoMorpho.

    The whole reference table is loaded and filtered in RAM, so the patient's diagnosis never goes into an SQL query.
    The local CSV is used only for development without IRIS configured.
    """
    if os.getenv("IRIS_HOST") and os.getenv("IRIS_PASSWORD"):
        try:
            from trialink import db
            conn = db.connect()
            try:
                rows = db.load_topo_morpho(conn)
            finally:
                conn.close()
        except Exception as exc:
            return None, f"IRIS nedostupné ({type(exc).__name__})"
        if not rows:
            return None, "tabulka TriaLink.TopoMorpho v IRIS je prázdná (spusťte python -m trialink.dictionaries)"
        return TopoMorphoIndex(rows), f"InterSystems IRIS · TriaLink.TopoMorpho ({len(rows)} kombinací)"
    try:
        rows, _, name, _ = load_file()
        return TopoMorphoIndex(rows), f"lokální soubor {name} (IRIS není nastaveno)"
    except FileNotFoundError:
        return None, "nedostupný"


def page_setup(title: str, prefix: str, *also_reset: str) -> None:
    st.set_page_config(page_title=f"TriaLink – {title}", page_icon="🔗", layout="wide")
    st.markdown("<style>.block-container{padding-top:1.2rem;max-width:1100px}</style>", unsafe_allow_html=True)
    last = st.session_state.get(f"{prefix}_last_seen")
    if last and time.time() - last > SESSION_TIMEOUT_S:
        for p in (prefix, *also_reset):
            reset(p)
        st.info("Relace vypršela – zadané údaje byly z paměti odstraněny.")
    st.session_state[f"{prefix}_last_seen"] = time.time()


def reset(prefix: str) -> None:
    for key in [k for k in st.session_state if k.startswith(prefix)]:
        del st.session_state[key]


def biomarker_rows(p: PatientProfile) -> list[dict]:
    return [{"marker": b.marker, "status": b.status, "varianta": b.variant or "",
             "hodnoty": ", ".join(f"{v:g}" for v in b.values), "rozpor": b.conflict} for b in p.biomarkers.values()]


def rows_to_biomarkers(rows: list[dict]) -> dict[str, BiomarkerResult]:
    out = {}
    for r in rows:
        marker = (r.get("marker") or "").strip()
        if not marker:
            continue
        values = []
        for part in str(r.get("hodnoty") or "").replace(";", ",").split(","):
            part = part.strip().replace("%", "")
            if part:
                try:
                    values.append(float(part.replace(" ", "")))
                except ValueError:
                    pass
        b = BiomarkerResult(marker.upper(), r.get("status") or "nevysetreno", (r.get("varianta") or "").strip() or None,
                            values, conflict=bool(r.get("rozpor")))
        out[b.marker] = b
    return out


def therapy_rows(p: PatientProfile) -> list[dict]:
    return [{"lék / režim": t.name, "režim": t.setting or ""} for t in (p.therapies or [])]


def rows_to_therapies(rows: list[dict]) -> list[TherapyEpisode]:
    return [TherapyEpisode(r["lék / režim"].strip(), r.get("režim") or None) for r in rows if (r.get("lék / režim") or "").strip()]


def biomarker_column_config():
    return {
        "marker": st.column_config.TextColumn("Biomarker", help="Např. KRAS, HER2, ER, PD-L1 TPS, PD-L1 CPS, TMB, dMMR/MSI-H, BRCA1"),
        "status": st.column_config.SelectboxColumn("Výsledek", options=list(BIOMARKER_STATUSES), default="pozitivni"),
        "varianta": st.column_config.TextColumn("Varianta", help="Např. G12C, G13D, V600E"),
        "hodnoty": st.column_config.TextColumn("Hodnota(y) %/skóre", help="Více hodnot oddělte čárkou (rozpor)"),
        "rozpor": st.column_config.CheckboxColumn("Rozpor"),
    }


def render_result(r: CohortResult, *, lay: bool = False) -> None:
    s = r.study
    st.markdown(f"**{STATUS_ICONS[r.status]} {s.acronym}** · {s.study_id} · _{r.cohort['label']}_")
    st.caption(f"{STATUS_LABELS[r.status]} · {s.indication}" + ("" if r.targeted else " · studie napříč diagnózami"))
    cols = st.columns(3)
    with cols[0]:
        st.markdown("**Splněno**" if not lay else "**Odpovídá**")
        for x in r.satisfied:
            st.markdown(f"{STATE_ICONS[x.state]} {x.reason}")
    with cols[1]:
        st.markdown("**K ověření**" if not lay else "**Je potřeba ověřit**")
        for x in r.open_items:
            text = x.criterion.get("value") if x.criterion["field"] == "manual" else x.reason
            st.markdown(f"{STATE_ICONS[x.state]} {text}")
    with cols[2]:
        st.markdown("**Nesplněno**" if not lay else "**Neodpovídá**")
        for x in r.failed:
            st.markdown(f"{STATE_ICONS[x.state]} {x.reason}")
    if not lay:
        with st.popover("Zdroj kritérií"):
            for x in r.results:
                st.markdown(f"{STATE_ICONS[x.state]} `{x.criterion['id']}` – „{x.criterion.get('quote', '')}“")
            st.caption(f"Stav pravidel: {s.review} (návrh = čeká na kontrolu onkologem)")


def disclaimer(lay: bool = False) -> None:
    if lay:
        st.caption("TriaLink pouze pomáhá s orientací. O vhodnosti studie vždy rozhoduje lékař. Výsledek není doporučení léčby.")
    else:
        st.caption("Předběžný screening podle strukturovaných kritérií (stav: návrh) a exportu SONIS k 29. 9. 2026. "
                   "Nenahrazuje posouzení zkoušejícím; aktuální nábor ověřte u studijního týmu.")


UNKNOWN = "— nevím / nezadáno —"


def _opt(options: dict) -> list:
    return [None, *options]


def _fmt(options: dict):
    return lambda v: UNKNOWN if v is None else options.get(v, v)


def apply_profile(prefix: str, p: PatientProfile) -> None:
    """Prefill widget state from a profile (demo case or extraction)."""
    icd = normalize_icd(p.icd10)
    ss = st.session_state
    ss[f"{prefix}_icd"] = icd
    ss[f"{prefix}_morpho"] = p.morphology
    ss[f"{prefix}_histology"] = p.histology
    ss[f"{prefix}_extent"] = p.disease_extent
    ss[f"{prefix}_setting"] = p.setting
    ss[f"{prefix}_lines_known"] = p.prior_palliative_lines is not None
    ss[f"{prefix}_lines"] = p.prior_palliative_lines or 0
    ss[f"{prefix}_her2"] = p.her2_status
    ss[f"{prefix}_ecog"] = p.ecog
    ss[f"{prefix}_age"] = p.age
    ss[f"{prefix}_sex"] = p.sex
    ss[f"{prefix}_swallow"] = {True: "ano", False: "ne"}.get(p.can_swallow)
    ss[f"{prefix}_therapy_known"] = p.therapies is not None
    ss[f"{prefix}_bio_init"] = biomarker_rows(p)
    ss[f"{prefix}_ther_init"] = therapy_rows(p)
    ss[f"{prefix}_conflicts"] = list(p.conflicts)
    ss[f"{prefix}_evidence"] = dict(p.evidence)
    ss[f"{prefix}_editor_rev"] = ss.get(f"{prefix}_editor_rev", 0) + 1


def profile_form(prefix: str, *, lay: bool = False) -> PatientProfile:
    """Decision tree: diagnosis -> stage -> treatment line -> biomarkers -> other gates."""
    ss = st.session_state
    tip = (lambda k: TOOLTIPS[k]) if lay else (lambda k: None)
    rev = ss.get(f"{prefix}_editor_rev", 0)
    evidence = ss.get(f"{prefix}_evidence", {})

    def src(name: str) -> None:
        if evidence.get(name):
            st.caption(f"Zdroj: „{evidence[name]}“")

    st.markdown("#### 1 · Diagnóza" if not lay else "#### 1 · Jakou máte diagnózu?")
    index, morpho_source = get_topo_morpho()
    icd_key, morpho_key, hist_key = f"{prefix}_icd", f"{prefix}_morpho", f"{prefix}_histology"
    codes = index.diagnosis_codes() if index else sorted(ICD_NAMES)
    current = normalize_icd(ss.get(icd_key))
    if ss.get(icd_key) != current:
        ss[icd_key] = current
    if current and current not in codes:
        codes = sorted({*codes, current})
    c1, c2 = st.columns(2)
    diagnosis = c1.selectbox(
        "Diagnóza (MKN-10)" if not lay else "Kde nádor vznikl (diagnóza)", [None, *codes], key=icd_key,
        format_func=lambda v: UNKNOWN if v is None else diagnosis_label(v),
        help=tip("mkn10") or "Začněte psát kód nebo orgán (např. C18 nebo prás) – seznam se vyfiltruje. "
                                "Tříznakový kód (C18) = lokalizace bez upřesnění.")

    allowed = index.morphologies(diagnosis) if index and diagnosis else []
    if ss.get(morpho_key) and ss[morpho_key] not in allowed:
        if diagnosis and index:
            st.warning(f"Morfologie {format_morpho(ss[morpho_key])} není pro diagnózu {diagnosis_label(diagnosis)} v číselníku povolena – vyberte znovu.")
        ss[morpho_key] = None
    morpho = c2.selectbox(
        "Morfologie (ICD-O-3)" if not lay else "Typ nádoru podle zprávy (morfologie)", [None, *allowed], key=morpho_key,
        format_func=lambda v: UNKNOWN if v is None else format_morpho(v), disabled=not allowed,
        help=(f"Nabízeny jsou jen morfologie povolené pro zvolenou diagnózu podle číselníku TNM skupin "
              f"(zdroj: {morpho_source}). Lze vyhledávat kódem i názvem, např. 8140 nebo adenokarcinom." if index
              else f"Číselník morfologií není dostupný: {morpho_source}."))
    src("icd10")
    src("morphology")

    derived = histology_from_morpho(morpho)
    if derived:
        histology = derived
        groups = index.group_names(diagnosis, morpho) if index else []
        st.caption(f"Histologický typ pro párování se studiemi: **{HISTOLOGIES[derived]}** (odvozeno z morfologie)"
                   + (f" · skupina TNM: {', '.join(groups)}" if groups else ""))
    else:
        histology = st.selectbox(
            "Histologický typ" + (" (nelze odvodit z morfologie)" if morpho else " (pokud neznáte morfologii)"),
            _opt(HISTOLOGIES), format_func=_fmt(HISTOLOGIES), key=hist_key,
            help=tip("histologie") or "Kritéria studií jsou formulována histologickým typem (např. adenokarcinom, skvamózní). "
                                      "Když zvolíte morfologii, typ se doplní automaticky.")
    if index and diagnosis and not allowed:
        st.caption(f"Kód {diagnosis_label(diagnosis)} není v číselníku TNM skupin – morfologii nelze vybrat.")
    elif not index:
        st.caption(f"⚠️ Číselník morfologií není dostupný: {morpho_source}.")
    src("histology")

    st.markdown("#### 2 · Rozsah onemocnění" if not lay else "#### 2 · Jak je nemoc rozsáhlá?")
    extent = st.radio("Rozsah", _opt(EXTENTS), format_func=_fmt(EXTENTS), key=f"{prefix}_extent", horizontal=True,
                      help=tip("rozsah"), label_visibility="collapsed")
    src("disease_extent")

    st.markdown("#### 3 · Léčebná situace a předchozí léčba" if not lay else "#### 3 · Jakou léčbu máte za sebou a co vás čeká?")
    setting = st.radio("Plánovaná léčba", _opt(SETTINGS), format_func=_fmt(SETTINGS), key=f"{prefix}_setting", horizontal=True,
                       help=tip("paliativni") if lay else None)
    c1, c2 = st.columns([1, 2])
    lines_known = c1.checkbox("Počet linií paliativní léčby znám", key=f"{prefix}_lines_known", help=tip("linie"))
    lines = c2.number_input("Již podané linie léčby pokročilého onemocnění", 0, 10, key=f"{prefix}_lines", disabled=not lines_known)
    therapy_known = st.checkbox("Dosavadní léčbu znám (i když žádná nebyla)", key=f"{prefix}_therapy_known")
    therapies = None
    if therapy_known:
        rows = st.data_editor(ss.get(f"{prefix}_ther_init", []) or [{"lék / režim": "", "režim": None}], num_rows="dynamic",
                              key=f"{prefix}_ther_{rev}", width="stretch", column_config={
                                  "lék / režim": st.column_config.TextColumn("Lék / režim", help="Např. FOLFOX + nivolumab, Kadcyla, Enhertu"),
                                  "režim": st.column_config.SelectboxColumn("Kdy", options=list(SETTINGS))})
        therapies = rows_to_therapies(rows)

    st.markdown("#### 4 · Biomarkery" if not lay else "#### 4 · Výsledky vyšetření nádoru (biomarkery)")
    if lay:
        st.caption(" · ".join(f"**{k}**: {TOOLTIPS[k]}" for k in ("KRAS", "HER2", "PD-L1")))
    her2 = st.selectbox("HER2", _opt(HER2_STATUSES), format_func=_fmt(HER2_STATUSES), key=f"{prefix}_her2", help=tip("HER2"))
    bio_rows = st.data_editor(ss.get(f"{prefix}_bio_init", []) or [{"marker": "", "status": "pozitivni", "varianta": "", "hodnoty": "", "rozpor": False}],
                              num_rows="dynamic", key=f"{prefix}_bio_{rev}", width="stretch", column_config=biomarker_column_config())

    st.markdown("#### 5 · Další kritéria" if not lay else "#### 5 · Celkový stav")
    c1, c2, c3, c4 = st.columns(4)
    ecog = c1.selectbox("ECOG", [None, 0, 1, 2, 3, 4], format_func=lambda v: UNKNOWN if v is None else str(v), key=f"{prefix}_ecog", help=tip("ECOG"))
    age = c2.number_input("Věk", 0, 120, key=f"{prefix}_age", **({} if f"{prefix}_age" in ss else {"value": None}))
    sex = c3.selectbox("Pohlaví", [None, "F", "M"], format_func=lambda v: {None: UNKNOWN, "F": "žena", "M": "muž"}[v], key=f"{prefix}_sex")
    swallow = c4.selectbox("Může polykat tablety?", [None, "ano", "ne"], format_func=lambda v: UNKNOWN if v is None else v,
                           key=f"{prefix}_swallow", help=tip("polykani"))

    p = PatientProfile(
        icd10=diagnosis, morphology=morpho, histology=histology, disease_extent=extent, setting=setting,
        prior_palliative_lines=int(lines) if lines_known else None, her2_status=her2,
        biomarkers=rows_to_biomarkers(bio_rows), therapies=therapies, ecog=ecog,
        age=int(age) if age is not None else None, sex=sex, can_swallow={"ano": True, "ne": False}.get(swallow),
        conflicts=list(ss.get(f"{prefix}_conflicts", [])), evidence=evidence,
    )
    if p.conflicts:
        st.warning("**Rozpory ve zdrojové zprávě:**\n" + "\n".join(f"- {c}" for c in p.conflicts))
    return p

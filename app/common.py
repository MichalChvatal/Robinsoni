"""Shared Streamlit helpers. Patient data lives only in st.session_state (server RAM) and is cleared on reset."""
from __future__ import annotations

import base64
import os
import sys
import time
from pathlib import Path

import streamlit as st
from dotenv import load_dotenv

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from trialink.biomarkers import RESULT_TYPES, BiomarkerDictionary, load_file as load_biomarker_file  # noqa: E402
from trialink.catalog import load_catalog  # noqa: E402
from trialink.dictionaries import ICD_NAMES, TopoMorphoIndex, diagnosis_label, format_morpho, histology_from_morpho, load_file  # noqa: E402
from trialink.matching import STATUS_CANDIDATE, STATUS_EXCLUDED, STATUS_LABELS, STATUS_VERIFY, CohortResult, sex_caution  # noqa: E402
from trialink.lay_terms import TOOLTIPS  # noqa: E402
from trialink.model import (  # noqa: E402
    BIOMARKER_STATUSES, EXTENTS, HER2_STATUSES, HISTOLOGIES, SETTINGS, SEX_LABELS, BiomarkerResult, PatientProfile, TherapyEpisode,
    normalize_icd, sex_for_diagnosis,
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


@st.cache_resource(show_spinner="Načítám číselník biomarkerů…", ttl=3600)
def get_biomarker_dictionary() -> tuple[BiomarkerDictionary, str]:
    """Allowed biomarker x result-type combinations from IRIS TriaLink.BiomarkerResultType, else the packaged CSV."""
    note = "IRIS není nastaveno"
    if os.getenv("IRIS_HOST") and os.getenv("IRIS_PASSWORD"):
        try:
            from trialink import db
            conn = db.connect()
            try:
                rows = db.load_biomarker_types(conn)
            finally:
                conn.close()
            if rows:
                return BiomarkerDictionary(rows), f"InterSystems IRIS · TriaLink.BiomarkerResultType ({len(rows)} kombinací)"
            note = "tabulka v IRIS je prázdná – spusťte python -m trialink.biomarkers"
        except Exception as exc:
            note = f"IRIS nedostupné ({type(exc).__name__})"
    rows, _, name, _ = load_biomarker_file()
    return BiomarkerDictionary(rows), f"lokální soubor {name} ({note})"


def page_setup(title: str, prefix: str, *also_reset: str) -> None:
    st.set_page_config(page_title=f"TriaLink – {title}", page_icon="🔗", layout="wide")
    st.markdown("<style>.block-container{padding-top:3.5rem;max-width:1100px}</style>", unsafe_allow_html=True)
    last = st.session_state.get(f"{prefix}_last_seen")
    if last and time.time() - last > SESSION_TIMEOUT_S:
        for p in (prefix, *also_reset):
            reset(p)
        st.info("Relace vypršela – zadané údaje byly z paměti odstraněny.")
    st.session_state[f"{prefix}_last_seen"] = time.time()


def reset(prefix: str) -> None:
    for key in [k for k in st.session_state if k.startswith(prefix)]:
        del st.session_state[key]


ROOT_LOGO = Path(__file__).resolve().parent.parent / "logo.svg"
LEGACY_LOGO = Path(__file__).resolve().parent / "static" / "mou_logo.svg"
LOGO = ROOT_LOGO if ROOT_LOGO.is_file() else LEGACY_LOGO


def brand_header(key: str, on_home=None) -> bool:
    """Header with MOÚ logo on the left and Trialink logo/title centered, both clickable."""
    st.markdown(f"""<style>
:root {{
  --brand-bg: #eef7fb;
  --brand-panel: rgba(255, 255, 255, 0.86);
  --brand-primary: #0b6a8d;
  --brand-primary-strong: #064d6b;
  --brand-accent: #3bc7d8;
  --brand-surface: #f6f9fc;
  --brand-ink: #182338;
  --brand-muted: #53657a;
  --brand-border: rgba(11, 106, 141, 0.14);
}}
.stApp {{
  background: radial-gradient(circle at top left, #f0fafe 0%, #eef4ff 32%, #f7f9fc 100%);
  color: var(--brand-ink);
}}
.block-container {{
  padding-top: 5rem !important;
  max-width: 1280px !important;
}}
[data-testid="stHeader"] {{
  background: linear-gradient(90deg, rgba(8, 94, 124, 0.98), rgba(18, 124, 165, 0.96));
  box-shadow: 0 8px 30px rgba(11, 62, 83, 0.18);
  border-bottom: 1px solid rgba(255,255,255,0.18);
  height: 64px !important;
}}
[data-testid="stHeader"] .stAppHeader {{
  background: transparent;
}}
[data-testid="stSidebar"] {{
  background: rgba(246,249,252,0.85);
  border-right: 1px solid rgba(11, 106, 141, 0.1);
}}
.st-key-{key} {{
  width: auto !important;
  display: flex; justify-content: center; align-items: center;
}}
.st-key-{key} button {{
  padding: 0.25rem 0.7rem; min-height: 0; background: transparent; border: none; color: white; font-weight: 700;
  box-shadow: none; border-radius: 999px;
}}
.st-key-{key} button p {{font-size: 1.3rem; font-weight: 800; line-height: 1.2; color: white; margin: 0;}}
div.stButton > button {{
  border-radius: 0.9rem; border: 1px solid rgba(11, 106, 141, 0.12); background: linear-gradient(180deg, #ffffff, #edf6fb);
  color: var(--brand-ink); box-shadow: 0 10px 22px rgba(11, 62, 83, 0.08); transition: transform .15s ease, box-shadow .15s ease;
}}
</style>""", unsafe_allow_html=True)

    logo_css = ""
    if ROOT_LOGO.is_file():
        logo_uri = "data:image/svg+xml;base64," + base64.b64encode(ROOT_LOGO.read_bytes()).decode()
        # Streamlit button labels cannot contain HTML, so the logo is drawn as the button background.
        logo_css = f""".st-key-{key} button {{
                width: 170px !important; height: 54px !important;
                background: url("{logo_uri}") center / contain no-repeat content-box padding-box #fff !important;
            }}
            .st-key-{key} button p {{
                position: absolute !important; width: 1px; height: 1px; overflow: hidden; clip: rect(0 0 0 0);
            }}"""

    st.markdown(
        f"""
        <style>
            .brand-header-bar {{
                position: fixed;
                left: 0;
                top: 0;
                right: 0;
                height: 64px;
                z-index: 1000001;
                background: linear-gradient(90deg, rgba(8, 94, 124, 0.98), rgba(18, 124, 165, 0.96));
                box-shadow: 0 8px 30px rgba(11, 62, 83, 0.18);
                border-bottom: 1px solid rgba(255,255,255,0.18);
            }}
            .brand-header-left {{
                position: fixed;
                left: 1.25rem;
                top: 8px;
                z-index: 1000002;
                display: inline-flex;
                align-items: center;
                justify-content: center;
                padding: 4px 8px;
                border-radius: 14px;
                background: #fff;
                box-shadow: 0 4px 18px rgba(0,0,0,0.08);
            }}
            .brand-header-left img {{
                display: block;
                height: 40px;
                width: auto;
            }}
            .st-key-{key} {{
                position: fixed !important;
                left: 50% !important;
                top: 5px !important;
                transform: translateX(-50%) !important;
                z-index: 1000003 !important;
            }}
            .st-key-{key} button {{
                padding: 0.3rem 0.5rem !important;
                min-height: 0 !important;
                background: #fff !important;
                border: none !important;
                border-radius: 15px !important;
                box-shadow: none !important;
            }}
            {logo_css}
            @media (max-width: 420px) {{
                .brand-header-left {{left: 0.6rem; top: 12px;}}
                .brand-header-left img {{height: 32px;}}
                .st-key-{key} {{left: auto !important; right: 3.2rem !important; top: 13px !important; transform: none !important;}}
                .st-key-{key} button {{width: 120px !important; height: 38px !important;}}
            }}
        </style>
        """,
        unsafe_allow_html=True,
    )

    st.markdown('<div class="brand-header-bar"></div>', unsafe_allow_html=True)

    if LEGACY_LOGO.is_file():
        mou_uri = "data:image/svg+xml;base64," + base64.b64encode(LEGACY_LOGO.read_bytes()).decode()
        st.markdown(
            f"<a class='brand-header-left' href='https://www.mou.cz' target='_blank' rel='noopener noreferrer'>"
            f"<img src='{mou_uri}' alt='Masarykův onkologický ústav' /></a>",
            unsafe_allow_html=True,
        )

    return st.button("TriaLink", key=key, on_click=on_home, help="Zpět na úvod")


def biomarker_rows(p: PatientProfile) -> list[dict]:
    bio_dict, _ = get_biomarker_dictionary()
    return [{"marker": bio_dict.canonical(b.marker) or b.marker, "status": b.status, "typ": b.result_type,
             "varianta": b.variant or "", "hodnoty": ", ".join(f"{v:g}" for v in b.values), "rozpor": b.conflict}
            for b in p.biomarkers.values()]


def rows_to_biomarkers(rows: list[dict], bio_dict: BiomarkerDictionary | None = None) -> dict[str, BiomarkerResult]:
    out = {}
    for r in rows:
        marker = (r.get("marker") or "").strip()
        if not marker:
            continue
        marker = (bio_dict.canonical(marker) if bio_dict else None) or marker
        values = []
        for part in str(r.get("hodnoty") or "").replace(";", ",").split(","):
            part = part.strip().replace("%", "")
            if part:
                try:
                    values.append(float(part.replace(" ", "")))
                except ValueError:
                    pass
        b = BiomarkerResult(marker.upper(), r.get("status") or "nevysetreno", (r.get("varianta") or "").strip() or None,
                            values, conflict=bool(r.get("rozpor")), result_type=r.get("typ") or None)
        out[b.marker] = b
    return out


def therapy_rows(p: PatientProfile) -> list[dict]:
    return [{"lék / režim": t.name, "režim": t.setting or ""} for t in (p.therapies or [])]


def rows_to_therapies(rows: list[dict]) -> list[TherapyEpisode]:
    return [TherapyEpisode(r["lék / režim"].strip(), r.get("režim") or None) for r in rows if (r.get("lék / režim") or "").strip()]


def biomarker_column_config(bio_dict: BiomarkerDictionary, extra_markers: list[str] = ()):
    markers = bio_dict.codes + sorted({m for m in extra_markers if m and m not in bio_dict.labels})
    return {
        "marker": st.column_config.SelectboxColumn("Biomarker", options=markers, format_func=bio_dict.label,
                                                   help="Výběr z číselníku biomarkerů (např. KRAS, ER, PD-L1 TPS, dMMR/MSI-H)"),
        "status": st.column_config.SelectboxColumn("Výsledek", options=list(BIOMARKER_STATUSES), default="pozitivni"),
        "typ": st.column_config.SelectboxColumn("Typ nálezu", options=list(RESULT_TYPES), format_func=RESULT_TYPES.get,
                                                help="Mutace / exprese / fúze / amplifikace / delece / metylace / jiný – "
                                                     "povolené typy pro daný biomarker určuje číselník"),
        "varianta": st.column_config.TextColumn("Varianta", help="Jen u typu Mutace / varianta, např. G12C, G13D, V600E"),
        "hodnoty": st.column_config.TextColumn("Hodnota(y) %/skóre", help="Více hodnot oddělte čárkou (rozpor)"),
        "rozpor": st.column_config.CheckboxColumn("Rozpor"),
    }


def render_result(r: CohortResult, *, lay: bool = False, profile: PatientProfile | None = None,
                  collapsed: bool = False, icon: str | None = None, group_label: str | None = None) -> None:
    s = r.study
    st.markdown(f"**{icon or STATUS_ICONS[r.status]} {s.acronym}** · {s.study_id} · _{r.cohort['label']}_")
    multi = "studie pro více druhů nádorů"
    show_multi = not r.targeted and multi not in (group_label or "").lower()
    st.caption((f"{group_label} · " if group_label else "") + f"{STATUS_LABELS[r.status]} · {s.indication}"
               + (f" · {multi}" if show_multi else ""))
    note = sex_caution(r, profile) if profile else None
    if note:
        st.warning(note)
    if collapsed:
        label = (f"Detail kritérií – splněno {len(r.satisfied)} · k ověření {len(r.open_items)} · "
                 f"nesplněno {len(r.failed)}")
        with st.expander(label, expanded=False):
            _render_criteria(r, lay=lay)
    else:
        _render_criteria(r, lay=lay)


def _render_criteria(r: CohortResult, *, lay: bool) -> None:
    s = r.study
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
# HER2 IHC status is routinely tested only for these sites (breast, oesophagus/stomach, colorectum, uterus).
HER2_SITES = ("C50", "C15", "C16", "C18", "C19", "C20", "C54")


def her2_relevant(diagnosis: str | None) -> bool:
    return bool(diagnosis) and diagnosis.startswith(HER2_SITES)


def _opt(options: dict) -> list:
    return [None, *options]


def _fmt(options: dict):
    return lambda v: UNKNOWN if v is None else options.get(v, v)


def apply_profile(prefix: str, p: PatientProfile) -> None:
    """Prefill widget state from a profile (demo case or extraction)."""
    ss = st.session_state
    therapy_mode = None if p.therapies is None else ("list" if p.therapies else "none")
    values = {
        "icd": normalize_icd(p.icd10), "morpho": p.morphology, "histology": p.histology, "extent": p.disease_extent,
        "setting": p.setting, "lines": p.prior_palliative_lines, "her2": p.her2_status, "ecog": p.ecog, "age": p.age,
        "sex": p.sex, "swallow": {True: "ano", False: "ne"}.get(p.can_swallow), "ther_mode": therapy_mode,
    }
    for name, value in values.items():
        # Missing facts stay unset so the widget falls back to its first option "nevím / nezadáno".
        if value is None:
            ss.pop(f"{prefix}_{name}", None)
        else:
            ss[f"{prefix}_{name}"] = value
    ss[f"{prefix}_bio_init"] = biomarker_rows(p)
    ss[f"{prefix}_ther_init"] = therapy_rows(p)
    ss[f"{prefix}_conflicts"] = list(p.conflicts)
    ss[f"{prefix}_evidence"] = dict(p.evidence)
    ss[f"{prefix}_editor_rev"] = ss.get(f"{prefix}_editor_rev", 0) + 1


SECTION_COLORS = ("#0b6a8d", "#1f8a70", "#7b4fa8", "#c76a12", "#b03a5b")
FIELD_BORDER = "#5f7f97"


def _form_css(prefix: str) -> str:
    """Each form section is a white card with its own accent colour; inputs are white with a clearly visible frame."""
    css = []
    for n, color in enumerate(SECTION_COLORS, start=1):
        s = f".st-key-{prefix}_sec{n}"
        css.append(f"""{s} {{
  background: #ffffff !important; border: 1px solid {color}33 !important; border-left: 7px solid {color} !important;
  border-radius: 14px !important; padding: 0.9rem 1.3rem 1.1rem !important; box-shadow: 0 6px 18px rgba(11,62,83,0.08);
  margin-bottom: 0.6rem;
}}
{s} h4 {{color: {color}; padding-top: 0.1rem; border-bottom: 2px solid {color}26; padding-bottom: 0.45rem; margin-bottom: 0.3rem;}}
{s} [data-testid="stSelectbox"] [role="group"], {s} [data-baseweb="input"], {s} [data-baseweb="textarea"],
{s} [data-testid="stNumberInputContainer"], {s} [data-testid="stDataFrameResizable"] {{
  background: #ffffff !important; border: 2px solid {FIELD_BORDER} !important; border-radius: 8px !important;
}}
{s} [data-testid="stNumberInputContainer"] [data-baseweb="input"] {{border: none !important;}}
{s} [data-testid="stSelectbox"] [role="group"]:has(input:disabled) {{border-color: #c3d0da !important; background: #f4f7f9 !important;}}
{s} input, {s} textarea {{background: #ffffff !important; color: #182338 !important;}}
{s} [data-testid="stSelectbox"] [role="group"]:hover, {s} [data-baseweb="input"]:hover,
{s} [data-testid="stNumberInputContainer"]:hover {{
  border-color: {color} !important;
}}
{s} [data-testid="stSelectbox"] [role="group"]:focus-within, {s} [data-baseweb="input"]:focus-within,
{s} [data-testid="stNumberInputContainer"]:focus-within {{
  border-color: {color} !important; box-shadow: 0 0 0 3px {color}33 !important;
}}
{s} [data-testid="stWidgetLabel"] p {{font-weight: 600; color: #182338;}}
{s} [data-testid="stRadioOption"] {{
  background: #ffffff; border: 2px solid {FIELD_BORDER}; border-radius: 999px; padding: 0.3rem 0.9rem 0.3rem 0.55rem;
  margin: 0.15rem 0.4rem 0.15rem 0;
}}
{s} [data-testid="stRadioOption"]:has(input:checked) {{
  border-color: {color}; background: {color}1a; box-shadow: 0 0 0 1px {color};
}}""")
    return "<style>" + "\n".join(css) + "</style>"


def profile_form(prefix: str, *, lay: bool = False) -> PatientProfile:
    """Decision tree: diagnosis -> stage -> treatment line -> biomarkers -> other gates."""
    ss = st.session_state
    st.markdown(_form_css(prefix), unsafe_allow_html=True)
    tip = (lambda k: TOOLTIPS[k]) if lay else (lambda k: None)
    rev = ss.get(f"{prefix}_editor_rev", 0)
    evidence = ss.get(f"{prefix}_evidence", {})

    def src(name: str) -> None:
        if evidence.get(name):
            st.caption(f"Zdroj: „{evidence[name]}“")

    with st.container(border=True, key=f"{prefix}_sec1"):
        st.markdown("#### 1 · Diagnóza" if not lay else "#### 1 · Jakou máte diagnózu?")
        index, morpho_source = get_topo_morpho()
        icd_key, morpho_key, hist_key = f"{prefix}_icd", f"{prefix}_morpho", f"{prefix}_histology"
        codes = index.diagnosis_codes() if index else sorted(ICD_NAMES)
        current = normalize_icd(ss.get(icd_key))
        if current is None:
            ss.pop(icd_key, None)
        elif ss.get(icd_key) != current:
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
            ss.pop(morpho_key, None)
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

    with st.container(border=True, key=f"{prefix}_sec2"):
        st.markdown("#### 2 · Rozsah onemocnění" if not lay else "#### 2 · Jak je nemoc rozsáhlá?")
        extent = st.radio("Rozsah", _opt(EXTENTS), format_func=_fmt(EXTENTS), key=f"{prefix}_extent", horizontal=True,
                          help=tip("rozsah"), label_visibility="collapsed")
        src("disease_extent")

    with st.container(border=True, key=f"{prefix}_sec3"):
        st.markdown("#### 3 · Léčebná situace a předchozí léčba" if not lay else "#### 3 · Vaše léčba")
        setting = ss.get(f"{prefix}_setting")
        lines = st.selectbox(
            "Počet již podaných linií léčby pokročilého onemocnění" if not lay
            else "Kolikrát už jste dostal(a) léčbu kvůli pokročilé (metastatické) nemoci?",
            [None, *range(0, 11)], key=f"{prefix}_lines", help=tip("linie"),
            format_func=lambda v: UNKNOWN if v is None else ("žádnou" if v == 0 else (f"{v}×" if lay else f"{v}. linie")))
        st.write("")
        therapy_modes = {"none": "Zatím žádná léčba" if lay else "Žádná předchozí léčba", "list": "Vypsat léky / režimy"}
        therapy_mode = st.radio(
            "Jakou léčbu už jste podstoupil(a)?" if lay else "Předchozí systémová léčba", _opt(therapy_modes),
            format_func=_fmt(therapy_modes), key=f"{prefix}_ther_mode", horizontal=True,
            help="Některé studie vyžadují nebo naopak vylučují určitou předchozí léčbu (např. FOLFOX, Kadcyla).")
        therapies = None if therapy_mode is None else []
        if therapy_mode == "list":
            rows = st.data_editor(ss.get(f"{prefix}_ther_init", []) or [{"lék / režim": "", "režim": None}], num_rows="dynamic",
                                  key=f"{prefix}_ther_{rev}", width="stretch", column_config={
                                      "lék / režim": st.column_config.TextColumn("Lék / režim", help="Např. FOLFOX + nivolumab, Kadcyla, Enhertu"),
                                      "režim": st.column_config.SelectboxColumn("Kdy", options=list(SETTINGS))})
            therapies = rows_to_therapies(rows)

    with st.container(border=True, key=f"{prefix}_sec4"):
        st.markdown("#### 4 · Biomarkery" if not lay else "#### 4 · Výsledky vyšetření nádoru (biomarkery)")
        if lay:
            st.caption(" · ".join(f"**{k}**: {TOOLTIPS[k]}" for k in ("KRAS", "HER2", "PD-L1")))
        her2 = None
        if her2_relevant(diagnosis) or ss.get(f"{prefix}_her2"):
            her2 = st.selectbox("HER2", _opt(HER2_STATUSES), format_func=_fmt(HER2_STATUSES), key=f"{prefix}_her2", help=tip("HER2"))
        bio_dict, bio_source = get_biomarker_dictionary()
        bio_init = ss.get(f"{prefix}_bio_init", []) or [{"marker": None, "status": "pozitivni", "typ": None, "varianta": "", "hodnoty": "", "rozpor": False}]
        bio_rows = st.data_editor(bio_init, num_rows="dynamic", key=f"{prefix}_bio_{rev}", width="stretch",
                                  column_config=biomarker_column_config(bio_dict, [r.get("marker") for r in bio_init]))
        biomarkers = rows_to_biomarkers(bio_rows, bio_dict)
        bio_issues = bio_dict.check_all(biomarkers.values())
        if bio_issues:
            st.warning(("**Zkontrolujte prosím zadané biomarkery:**\n" if lay else "**Neobvyklá kombinace podle číselníku biomarkerů** "
                        "(údaj se přesto použije):\n") + "\n".join(f"- {i}" for i in bio_issues))
        if not lay:
            with st.expander("Povolené kombinace biomarker × typ nálezu"):
                st.dataframe([{"Biomarker": bio_dict.label(c), "Povolené typy nálezu": bio_dict.describe(c)} for c in bio_dict.codes],
                             hide_index=True, width="stretch")
                st.caption(f"Zdroj číselníku: {bio_source}")

    with st.container(border=True, key=f"{prefix}_sec5"):
        st.markdown("#### 5 · Další kritéria" if not lay else "#### 5 · Celkový stav")
        implied_sex = sex_for_diagnosis(diagnosis)
        if implied_sex and f"{prefix}_sex" not in ss:
            ss[f"{prefix}_sex"] = implied_sex
        c1, c2, c3, c4 = st.columns(4)
        ecog = c1.selectbox("ECOG", [None, 0, 1, 2, 3, 4], format_func=lambda v: UNKNOWN if v is None else str(v), key=f"{prefix}_ecog", help=tip("ECOG"))
        age = c2.number_input("Věk", 0, 120, key=f"{prefix}_age", **({} if f"{prefix}_age" in ss else {"value": None}))
        sex = c3.selectbox("Pohlaví", [None, "F", "M"], format_func=lambda v: {None: UNKNOWN, "F": "žena", "M": "muž"}[v], key=f"{prefix}_sex")
        swallow = c4.selectbox("Může polykat tablety?", [None, "ano", "ne"], format_func=lambda v: UNKNOWN if v is None else v,
                               key=f"{prefix}_swallow", help=tip("polykani"))
        if implied_sex and sex and sex != implied_sex:
            st.warning(f"Diagnóza {diagnosis_label(diagnosis)} se týká pouze pohlaví „{SEX_LABELS[implied_sex]}“ – "
                       "zkontrolujte prosím diagnózu nebo pohlaví.")

    p = PatientProfile(
        icd10=diagnosis, morphology=morpho, histology=histology, disease_extent=extent, setting=setting,
        prior_palliative_lines=lines, her2_status=her2,
        biomarkers=biomarkers, therapies=therapies, ecog=ecog,
        age=int(age) if age is not None else None, sex=sex, can_swallow={"ano": True, "ne": False}.get(swallow),
        conflicts=list(ss.get(f"{prefix}_conflicts", [])), evidence=evidence,
    )
    if p.conflicts and not lay:
        st.warning("**Rozpory ve zdrojové zprávě:**\n" + "\n".join(f"- {c}" for c in p.conflicts))
    return p

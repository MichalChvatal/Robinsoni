"""Quick one-row entry for the doctor: one active parameter at a time, auto-advance, no scrolling.

Values live in plain session keys (``{prefix}_quick``), never only in widget keys, because Streamlit drops the state
of widgets that are not rendered in a run. Patient data stays in st.session_state (RAM) only.
"""
from __future__ import annotations

import streamlit as st

from common import UNKNOWN, get_biomarker_dictionary, get_topo_morpho, her2_relevant, rows_to_biomarkers
from trialink.dictionaries import ICD_NAMES, diagnosis_label, format_morpho, histology_from_morpho
from trialink.model import (
    BIOMARKER_STATUSES, SEX_LABELS, BiomarkerResult, PatientProfile, TherapyEpisode, normalize_icd, normalize_variant,
    sex_for_diagnosis,
)

FIELDS: tuple[str, ...] = ("icd", "morpho", "histology", "extent", "lines", "therapies", "her2", "biomarkers",
                           "ecog", "age", "sex", "swallow")

LABELS = {
    "icd": "Diagnóza (MKN-10)", "morpho": "Morfologie (ICD-O-3)", "histology": "Histologický typ",
    "extent": "Rozsah onemocnění", "lines": "Linie paliativní léčby", "therapies": "Předchozí systémová léčba",
    "her2": "HER2", "biomarkers": "Biomarkery", "ecog": "ECOG", "age": "Věk", "sex": "Pohlaví",
    "swallow": "Polyká tablety?",
}
NAMES = {
    "icd": "diagnóza", "morpho": "morfologie", "histology": "histologie", "extent": "rozsah", "lines": "linie",
    "therapies": "předchozí léčba", "her2": "HER2", "biomarkers": "biomarkery", "ecog": "ECOG", "age": "věk",
    "sex": "pohlaví", "swallow": "polykání",
}
EVIDENCE_KEYS = {
    "icd": ("icd10",), "morpho": ("morphology",), "histology": ("histology",), "extent": ("disease_extent",),
    "lines": ("prior_palliative_lines", "setting"), "her2": ("her2_status",), "ecog": ("ecog",),
    "age": ("age", "birth_year"), "sex": ("sex",), "swallow": ("can_swallow",),
}
SHORT_HISTOLOGIES = {
    "adenokarcinom": "adenokarcinom", "skvamozni": "skvamózní", "nemalobunecny_nsclc": "NSCLC NOS",
    "velkobunecny": "velkobuněčný", "malobunecny": "malobuněčný", "duktalni_nst": "duktální NST",
    "lobularni": "lobulární", "svetlobunecny": "světlobuněčný", "jiny": "jiný",
}
SHORT_EXTENTS = {
    "lokalizovane_resekabilni": "lokalizované / resekabilní", "lokalne_pokrocile_neresekabilni": "lokálně pokročilé",
    "metastaticke": "metastatické",
}
SHORT_HER2 = {"pozitivni": "pozitivní (3+ / ISH+)", "low": "low (1+ / 2+ ISH−)", "ultralow": "ultralow", "0": "0"}
SHORT_STATUS = {"pozitivni": "+", "negativni": "−", "nelze": " nelze", "nevysetreno": " nevyš."}
SWALLOW = {True: "ano", False: "ne"}
NO_THERAPY = "Žádná předchozí léčba"
LIST_FIELDS = ("therapies", "biomarkers")

ss = st.session_state


# ---------- pure logic ----------

def relevant_fields(values: dict, allowed_morphs) -> list[str]:
    icd = values.get("icd")
    out = []
    for f in FIELDS:
        if f == "morpho" and not (icd and allowed_morphs):
            continue
        if f == "histology" and histology_from_morpho(values.get("morpho")):
            continue
        # A HER2 status or a sex conflicting with the diagnosis stays editable (mirrors profile_form).
        if f == "her2" and not (her2_relevant(icd) or values.get("her2")):
            continue
        implied = sex_for_diagnosis(icd)
        if f == "sex" and implied and values.get("sex") in (None, implied):
            continue
        out.append(f)
    return out


def next_field(values: dict, answered, allowed_morphs) -> str | None:
    return next((f for f in relevant_fields(values, allowed_morphs) if f not in answered), None)


def _known(values: dict, field: str) -> bool:
    if field == "biomarkers":
        return bool(values.get("biomarkers"))
    return values.get(field) is not None


def _parse_values(text: str) -> list[float] | None:
    """'50 %', '1, 50' -> numbers; anything else (e.g. G12C) -> None (= variant)."""
    parts = [x.strip().replace("%", "").replace(" ", "") for x in text.replace(";", ",").split(",")]
    parts = [x for x in parts if x]
    try:
        return [float(x.replace(",", ".")) for x in parts] if parts else None
    except ValueError:
        return None


# ---------- store ----------

def _k(prefix: str, name: str) -> str:
    return f"{prefix}_{name}"


def _allowed(icd: str | None) -> list[str]:
    index, _ = get_topo_morpho()
    return index.morphologies(icd) if index and icd else []


def load_profile(prefix: str, p: PatientProfile | None) -> None:
    """(Re)initialise the quick store from a profile; known values count as answered, None values will be asked."""
    p = p or PatientProfile()
    icd = normalize_icd(p.icd10)
    allowed = _allowed(icd)
    store = {
        "icd": icd, "morpho": p.morphology if p.morphology and p.morphology in allowed else None,
        "histology": p.histology, "extent": p.disease_extent, "lines": p.prior_palliative_lines,
        "therapies": None if p.therapies is None else list(p.therapies), "her2": p.her2_status,
        "biomarkers": dict(p.biomarkers), "ecog": p.ecog, "age": p.age, "sex": p.sex, "swallow": p.can_swallow,
        "setting": p.setting, "conflicts": list(p.conflicts), "evidence": dict(p.evidence), "sex_auto": False,
    }
    answered = {f for f in FIELDS if _known(store, f)}
    implied = sex_for_diagnosis(icd)
    if implied and store["sex"] is None:
        store["sex"], store["sex_auto"] = implied, True
        answered.discard("sex")
    ss[_k(prefix, "quick")] = store
    ss[_k(prefix, "quick_answered")] = answered
    ss.pop(_k(prefix, "quick_notice"), None)
    _goto(prefix, next_field(store, answered, allowed))


def sync_profile(prefix: str, p: PatientProfile | None) -> None:
    """Reload the store when the shared ``{prefix}_profile`` was changed elsewhere (e.g. in the full form).

    Fields whose value did not change keep their answered state, so "Nevím" answers are not asked again.
    """
    key = _k(prefix, "quick")
    if key in ss and ss.get(_k(prefix, "quick_synced")) == p:
        return
    old, old_answered = ss.get(key), set(ss.get(_k(prefix, "quick_answered")) or ())
    load_profile(prefix, p)
    if old is None:
        return
    s, answered = ss[key], ss[_k(prefix, "quick_answered")]
    answered |= {f for f in old_answered if f in FIELDS and old.get(f) == s.get(f)}
    if old.get("sex_auto") and old.get("sex") == s["sex"] and "sex" not in old_answered:
        s["sex_auto"] = True
        answered.discard("sex")
    _goto(prefix, next_field(s, answered, _allowed(s["icd"])))


def _ensure(prefix: str) -> tuple[dict, set]:
    if _k(prefix, "quick") not in ss:
        load_profile(prefix, ss.get(_k(prefix, "profile")))
    answered = ss.get(_k(prefix, "quick_answered"))
    if not isinstance(answered, set):
        answered = set(answered or ())
        ss[_k(prefix, "quick_answered")] = answered
    return ss[_k(prefix, "quick")], answered


def current_profile(prefix: str) -> PatientProfile:
    s, answered = _ensure(prefix)
    icd = s["icd"]
    sex = s["sex"]
    if sex is None and "sex" not in answered:
        sex = sex_for_diagnosis(icd)
    return PatientProfile(
        icd10=icd, morphology=s["morpho"], histology=histology_from_morpho(s["morpho"]) or s["histology"],
        disease_extent=s["extent"], setting=s["setting"], prior_palliative_lines=s["lines"], her2_status=s["her2"],
        biomarkers=dict(s["biomarkers"]), therapies=None if s["therapies"] is None else list(s["therapies"]),
        ecog=s["ecog"], age=int(s["age"]) if s["age"] is not None else None, sex=sex, can_swallow=s["swallow"],
        conflicts=list(s["conflicts"]), evidence=s["evidence"],
    )


def _goto(prefix: str, field: str | None) -> None:
    ss[_k(prefix, "quick_field")] = field
    ss[_k(prefix, "quick_rev")] = ss.get(_k(prefix, "quick_rev"), 0) + 1


def _advance(prefix: str) -> None:
    s, answered = _ensure(prefix)
    _goto(prefix, next_field(s, answered, _allowed(s["icd"])))


def _apply_icd(prefix: str, s: dict, answered: set, icd: str | None) -> None:
    icd = normalize_icd(icd)
    changed = icd != s["icd"]
    s["icd"] = icd
    allowed = _allowed(icd)
    if s["morpho"] and s["morpho"] not in allowed:
        ss[_k(prefix, "quick_notice")] = (f"Morfologie {format_morpho(s['morpho'])} není pro novou diagnózu v číselníku "
                                          "povolena – vyberte znovu.")
        s["morpho"] = None
        answered.discard("morpho")
    elif changed and s["morpho"] is None:
        answered.discard("morpho")
    if not her2_relevant(icd):
        s["her2"] = None
        answered.discard("her2")
    implied = sex_for_diagnosis(icd)
    if implied and ("sex" not in answered or s.get("sex_auto")):
        s["sex"], s["sex_auto"] = implied, True
        answered.discard("sex")
    elif not implied and s.get("sex_auto"):
        s["sex"], s["sex_auto"] = None, False


def _set(prefix: str, field: str, value, advance: bool = True) -> None:
    s, answered = _ensure(prefix)
    if field == "icd":
        _apply_icd(prefix, s, answered, value)
    else:
        s[field] = value
    if field == "sex":
        s["sex_auto"] = False
    answered.add(field)
    if advance:
        _advance(prefix)


# ---------- callbacks ----------

def _on_widget(prefix: str, field: str, key: str) -> None:
    value = ss.get(key)
    if value is None:  # re-clicking the selected segment / clearing = keep the current value
        s, answered = _ensure(prefix)
        answered.add(field)
        _advance(prefix)
        return
    if field == "swallow":
        value = value == "ano"
    elif field == "age":
        value = int(value)
    _set(prefix, field, value)


def _on_unknown(prefix: str, field: str) -> None:
    if field == "biomarkers":
        _ensure(prefix)[1].add(field)
        _advance(prefix)
        return
    _set(prefix, field, None)


def _on_keep(prefix: str, field: str) -> None:
    _ensure(prefix)[1].add(field)
    _advance(prefix)


def _on_prev(prefix: str) -> None:
    s, _ = _ensure(prefix)
    fields = relevant_fields(s, _allowed(s["icd"]))
    active = ss.get(_k(prefix, "quick_field"))
    if active in fields:
        idx = fields.index(active)
        if idx > 0:
            _goto(prefix, fields[idx - 1])
    elif fields:
        _goto(prefix, fields[-1])


def _on_chip(prefix: str, field: str) -> None:
    _goto(prefix, field)


def _on_no_therapy(prefix: str, key: str) -> None:
    if ss.get(key):
        _set(prefix, "therapies", [])


def _on_therapy_text(prefix: str, key: str) -> None:
    name = (ss.get(key) or "").strip()
    if not name:
        return
    s, _ = _ensure(prefix)
    s["therapies"] = [*(s["therapies"] or []), TherapyEpisode(name, None)]
    ss[key] = ""


def _on_therapy_del(prefix: str, i: int) -> None:
    s, _ = _ensure(prefix)
    items = list(s["therapies"] or [])
    if 0 <= i < len(items):
        items.pop(i)
    s["therapies"] = items


def _on_done(prefix: str, field: str, bio_keys: tuple[str, str, str] | None = None) -> None:
    if bio_keys:
        _add_biomarker(prefix, *bio_keys)
    _ensure(prefix)[1].add(field)
    _advance(prefix)


def _add_biomarker(prefix: str, marker_key: str, status_key: str, detail_key: str) -> bool:
    marker = ss.get(marker_key)
    if not marker:
        return False
    detail = (ss.get(detail_key) or "").strip()
    numbers = _parse_values(detail) if detail else None
    variant = "" if numbers is not None or not detail else detail
    row = {"marker": marker, "status": ss.get(status_key) or "pozitivni", "typ": "mutace" if variant else None,
           "varianta": variant, "hodnoty": ", ".join(f"{v:g}" for v in numbers or []), "rozpor": False}
    bio_dict, _ = get_biomarker_dictionary()
    s, answered = _ensure(prefix)
    s["biomarkers"] = {**s["biomarkers"], **rows_to_biomarkers([row], bio_dict)}
    answered.discard("biomarkers")
    _goto(prefix, "biomarkers")  # new rev = fresh empty inputs, focus back on the marker
    return True


def _on_bio_add(prefix: str, marker_key: str, status_key: str, detail_key: str) -> None:
    _add_biomarker(prefix, marker_key, status_key, detail_key)


def _on_bio_del(prefix: str, marker: str) -> None:
    s, _ = _ensure(prefix)
    s["biomarkers"] = {k: v for k, v in s["biomarkers"].items() if k != marker}


# ---------- summaries ----------

def _bio_label(b: BiomarkerResult) -> str:
    variant = normalize_variant(b.variant)
    if variant:
        text = f"{b.marker} {variant}" + ("" if b.status == "pozitivni" else SHORT_STATUS.get(b.status, ""))
    else:
        text = f"{b.marker}{SHORT_STATUS.get(b.status, '')}"
    if b.values:
        text += " " + "/".join(f"{v:g}" for v in b.values)
    return text + (" ⚠" if b.conflict else "")


def _short(text: str, n: int = 34) -> str:
    return text if len(text) <= n else text[: n - 1] + "…"


def _summary(s: dict, field: str) -> str | None:
    v = s.get(field)
    if field == "biomarkers":
        return _short(" · ".join(_bio_label(b) for b in v.values()), 48) if v else None
    if v is None:
        return None
    if field == "icd":
        return _short(diagnosis_label(v))
    if field == "morpho":
        return _short(format_morpho(v), 30)
    if field == "histology":
        return SHORT_HISTOLOGIES.get(v, v)
    if field == "extent":
        return SHORT_EXTENTS.get(v, v)
    if field == "lines":
        return "0 linií" if v == 0 else f"{v}. linie"
    if field == "therapies":
        return _short(", ".join(t.name for t in v), 40) if v else "bez předchozí léčby"
    if field == "her2":
        return f"HER2 {SHORT_HER2.get(v, v).split(' (')[0]}"
    if field == "ecog":
        return f"ECOG {v}"
    if field == "age":
        return f"{v} let"
    if field == "sex":
        return SEX_LABELS.get(v, v) + (" (dle dg.)" if s.get("sex_auto") else "")
    if field == "swallow":
        return "polyká" if v else "nepolyká"
    return str(v)


# ---------- rendering ----------

def _css(prefix: str) -> str:
    q = f".st-key-{prefix}_quick"
    return f"""<style>
{q} {{
  background: #ffffff !important; border: 1px solid #0b6a8d33 !important; border-left: 7px solid #0b6a8d !important;
  border-radius: 14px !important; padding: 0.55rem 0.9rem 0.5rem !important; box-shadow: 0 6px 18px rgba(11,62,83,0.08);
  gap: 0.35rem !important;
}}
{q} .q-label {{font-weight: 700; font-size: 1.02rem; line-height: 1.25; color: #182338; margin: 0.35rem 0 0;}}
{q} .q-prog {{display: inline-block; font-size: 0.78rem; color: #53657a; font-weight: 600; margin-top: 0.1rem;}}
{q} [data-testid="stSelectbox"] [role="group"], {q} [data-baseweb="input"], {q} [data-testid="stNumberInputContainer"] {{
  background: #ffffff !important; border: 2px solid #5f7f97 !important; border-radius: 8px !important;
}}
{q} [data-testid="stNumberInputContainer"] [data-baseweb="input"] {{border: none !important;}}
{q} [data-testid="stSelectbox"] [role="group"]:focus-within, {q} [data-baseweb="input"]:focus-within,
{q} [data-testid="stNumberInputContainer"]:focus-within {{border-color: #0b6a8d !important; box-shadow: 0 0 0 3px #0b6a8d33 !important;}}
{q} input {{background: #ffffff !important; color: #182338 !important;}}
{q} div.stButton > button {{min-height: 0; padding: 0.3rem 0.7rem; box-shadow: none;}}
{q} [data-testid="stCaptionContainer"] p {{margin: 0;}}
{q} [data-testid="stAlert"] {{padding: 0.1rem 0;}}
{q} [data-testid="stAlertContainer"] {{padding: 0.45rem 0.8rem;}}
.st-key-{prefix}_qchips {{gap: 0.3rem !important;}}
.st-key-{prefix}_qchips div.stButton > button {{
  min-height: 0; padding: 0.05rem 0.6rem; border-radius: 999px; background: #eef6fb; border: 1px solid #0b6a8d33;
  box-shadow: none;
}}
.st-key-{prefix}_qchips div.stButton > button p {{font-size: 0.8rem; line-height: 1.3;}}
.st-key-{prefix}_qchips div.stButton > button[kind="primary"] {{background: #0b6a8d; color: #fff; border-color: #0b6a8d;}}
.st-key-{prefix}_qchips div.stButton > button[kind="primary"] p {{color: #fff;}}
[class*="st-key-{prefix}_qdel_"] button {{
  min-height: 0 !important; padding: 0.05rem 0.55rem !important; border-radius: 999px !important;
  background: #e6f4ee !important; border: 1px solid #1f8a7055 !important; box-shadow: none !important;
}}
[class*="st-key-{prefix}_qdel_"] button p {{font-size: 0.8rem;}}
.st-key-{prefix}_quick_js {{
  position: absolute !important; width: 1px !important; height: 1px !important; overflow: hidden; opacity: 0;
  pointer-events: none;
}}
</style>"""


def _segmented(prefix: str, field: str, key: str, options: dict, current) -> None:
    st.segmented_control(LABELS[field], list(options), format_func=options.get, key=key,
                         default=current if current in options else None, label_visibility="collapsed",
                         on_change=_on_widget, args=(prefix, field, key))


def _widget(prefix: str, field: str, s: dict, rev: int) -> None:
    key = f"{prefix}_q_{field}_{rev}"
    v = s.get(field)
    if field == "icd":
        index, _ = get_topo_morpho()
        codes = index.diagnosis_codes() if index else sorted(ICD_NAMES)
        if v and v not in codes:
            codes = sorted({*codes, v})
        st.selectbox(LABELS[field], codes, index=codes.index(v) if v in codes else None, key=key,
                     format_func=diagnosis_label, placeholder="Začněte psát kód nebo orgán…",
                     label_visibility="collapsed", on_change=_on_widget, args=(prefix, field, key))
    elif field == "morpho":
        allowed = _allowed(s["icd"])
        st.selectbox(LABELS[field], allowed, index=allowed.index(v) if v in allowed else None, key=key,
                     format_func=format_morpho, placeholder="Kód nebo název, např. 8140 nebo adenokarcinom…",
                     label_visibility="collapsed", on_change=_on_widget, args=(prefix, field, key))
    elif field == "histology":
        _segmented(prefix, field, key, SHORT_HISTOLOGIES, v)
    elif field == "extent":
        _segmented(prefix, field, key, SHORT_EXTENTS, v)
    elif field == "lines":
        _segmented(prefix, field, key, {n: "žádná" if n == 0 else f"{n}." for n in range(0, 11)}, v)
    elif field == "her2":
        _segmented(prefix, field, key, SHORT_HER2, v)
    elif field == "ecog":
        _segmented(prefix, field, key, {n: str(n) for n in range(0, 5)}, v)
    elif field == "sex":
        _segmented(prefix, field, key, SEX_LABELS, v)
    elif field == "swallow":
        _segmented(prefix, field, key, {"ano": "ano", "ne": "ne"}, SWALLOW.get(v))
    elif field == "age":
        st.number_input(LABELS[field], 0, 120, value=v, step=1, key=key, placeholder="Věk v letech + Enter",
                        label_visibility="collapsed", on_change=_on_widget, args=(prefix, field, key))
    elif field == "therapies":
        _therapies(prefix, s, key)
    elif field == "biomarkers":
        _biomarkers(prefix, s, rev)


def _therapies(prefix: str, s: dict, key: str) -> None:
    items = s["therapies"] or []
    with st.container(horizontal=True, vertical_alignment="center", gap="small"):
        none_key = f"{key}_none"
        st.segmented_control(LABELS["therapies"], [NO_THERAPY], key=none_key, label_visibility="collapsed",
                             default=NO_THERAPY if s["therapies"] == [] else None,
                             on_change=_on_no_therapy, args=(prefix, none_key))
        st.text_input("Lék / režim", key=key, placeholder="Lék / režim + Enter (např. FOLFOX)", width=240,
                      label_visibility="collapsed", on_change=_on_therapy_text, args=(prefix, key))
        for i, t in enumerate(items):
            st.button(f"{_short(t.name, 24)} ✕", key=f"{prefix}_qdel_ther_{i}", on_click=_on_therapy_del,
                      args=(prefix, i), help="Odebrat")
        st.button("Hotovo", key=f"{prefix}_q_done", type="primary", on_click=_on_done, args=(prefix, "therapies"))


def _biomarkers(prefix: str, s: dict, rev: int) -> None:
    bio_dict, _ = get_biomarker_dictionary()
    markers = bio_dict.codes + sorted({m for m in s["biomarkers"] if m and m not in bio_dict.labels})
    keys = (f"{prefix}_q_bio_marker_{rev}", f"{prefix}_q_bio_status_{rev}", f"{prefix}_q_bio_detail_{rev}")
    with st.container(horizontal=True, vertical_alignment="center", gap="small"):
        st.selectbox("Biomarker", markers, index=None, key=keys[0], format_func=bio_dict.label,
                     placeholder="Biomarker…", label_visibility="collapsed", width=180)
        st.selectbox("Výsledek", list(BIOMARKER_STATUSES), key=keys[1], format_func=BIOMARKER_STATUSES.get,
                     label_visibility="collapsed", width=125)
        st.text_input("Varianta / hodnota", key=keys[2], placeholder="Varianta / hodnota (G12C, 50 %)",
                      label_visibility="collapsed", width=165, on_change=_on_bio_add, args=(prefix, *keys))
        st.button("Přidat", key=f"{prefix}_q_bio_add", on_click=_on_bio_add, args=(prefix, *keys))
        st.button("Hotovo", key=f"{prefix}_q_done", type="primary", on_click=_on_done,
                  args=(prefix, "biomarkers", keys))
    if s["biomarkers"]:
        with st.container(horizontal=True, gap="small"):
            for i, (code, b) in enumerate(s["biomarkers"].items()):
                st.button(f"{_bio_label(b)} ✕", key=f"{prefix}_qdel_bio_{i}", on_click=_on_bio_del, args=(prefix, code),
                          help="Odebrat")
        issues = bio_dict.check_all(s["biomarkers"].values())
        if issues:
            st.caption("⚠️ " + " · ".join(issues))


def _chips(prefix: str, s: dict, answered: set, active: str | None, allowed) -> None:
    relevant = relevant_fields(s, allowed)
    derived = histology_from_morpho(s["morpho"])
    with st.container(horizontal=True, gap="small", key=f"{prefix}_qchips"):
        for f in FIELDS:
            target = f
            if f == "histology" and derived:
                label, target = f"{SHORT_HISTOLOGIES.get(derived, derived)} (z morfologie)", "morpho"
            elif _known(s, f) and (f in relevant or f in answered or f == "sex"):
                label = _summary(s, f)
            elif f in answered and f in relevant:
                label = f"? {NAMES[f]}"
            else:
                continue
            st.button(label, key=f"{prefix}_qchip_{f}", type="primary" if f == active else "secondary",
                      on_click=_on_chip, args=(prefix, target), help=f"Upravit: {LABELS[f]}")


def _focus_script(prefix: str, rev: int) -> None:
    # The rev comment makes the snippet unique per field change so the browser runs it again.
    with st.container(key=f"{prefix}_quick_js"):
        st.iframe(f"""<script>/* {prefix} {rev} */
let tries = 0;
const timer = setInterval(() => {{
  const box = window.parent.document.querySelector('.st-key-{prefix}_qw_{rev}');
  if (!box && ++tries < 40) return;
  clearInterval(timer);
  if (!box) return;
  const el = box.querySelector('input:not([disabled]):not([type="hidden"])') || box.querySelector('button');
  if (el) el.focus({{preventScroll: true}});
}}, 100);</script>""", height=1)


def quick_form(prefix: str) -> PatientProfile:
    """One-row quick entry for the doctor; returns the current profile."""
    s, answered = _ensure(prefix)
    allowed = _allowed(s["icd"])
    relevant = relevant_fields(s, allowed)
    active = ss.get(_k(prefix, "quick_field"))
    if active is not None and active not in relevant:
        _goto(prefix, next_field(s, answered, allowed))
        active = ss[_k(prefix, "quick_field")]
    rev = ss.get(_k(prefix, "quick_rev"), 0)

    st.markdown(_css(prefix), unsafe_allow_html=True)
    with st.container(border=True, key=f"{prefix}_quick"):
        c_label, c_widget, c_actions = st.columns([1.3, 6.2, 1.7], vertical_alignment="center")
        with c_label:
            if active:
                st.markdown(f"<div class='q-label'>{LABELS[active]}</div>"
                            f"<span class='q-prog'>{relevant.index(active) + 1}/{len(relevant)}</span>",
                            unsafe_allow_html=True)
            else:
                st.markdown(f"<div class='q-label'>Hotovo</div><span class='q-prog'>{len(relevant)}/{len(relevant)}</span>",
                            unsafe_allow_html=True)
        with c_widget:
            with st.container(key=f"{prefix}_qw_{rev}"):
                if active:
                    _widget(prefix, active, s, rev)
                else:
                    st.success("Vše zadáno – můžete vyhledat studie")
        with c_actions:
            with st.container(horizontal=True, gap="small", horizontal_alignment="right"):
                if active:
                    st.button("Nevím", key=f"{prefix}_q_unknown", on_click=_on_unknown, args=(prefix, active),
                              help=f"{LABELS[active]}: {UNKNOWN}")
                st.button("← Předchozí", key=f"{prefix}_q_prev", on_click=_on_prev, args=(prefix,),
                          disabled=bool(active) and relevant.index(active) == 0)
                if active and active in answered and active not in LIST_FIELDS:
                    st.button("Dál →", key=f"{prefix}_q_keep", on_click=_on_keep, args=(prefix, active),
                              help="Ponechat současnou hodnotu")

        notice = ss.pop(_k(prefix, "quick_notice"), None)
        if notice:
            st.caption(f"⚠️ {notice}")
        if active:
            evidence = s["evidence"]
            quote = next((evidence[k] for k in EVIDENCE_KEYS.get(active, ()) if evidence.get(k)), None)
            if quote:
                st.caption(f"Zdroj: „{quote}“")
        implied = sex_for_diagnosis(s["icd"])
        if implied and s["sex"] and s["sex"] != implied:
            st.caption(f"⚠️ Diagnóza {diagnosis_label(s['icd'])} se týká pouze pohlaví „{SEX_LABELS[implied]}“ – "
                       "zkontrolujte diagnózu nebo pohlaví.")
        _chips(prefix, s, answered, active, allowed)
        if s["conflicts"]:
            st.warning("**Rozpory ve zprávě:** " + " · ".join(s["conflicts"]))
        if active:
            _focus_script(prefix, rev)
    profile = current_profile(prefix)
    ss[_k(prefix, "quick_synced")] = profile
    return profile

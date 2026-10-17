import json
import os
import time

import streamlit as st

from common import brand_header, get_catalog, page_setup, reset
from trialink import sonis
from trialink.auth import Throttle, verify_password
from trialink.catalog import load_catalog, study_from_record, validate
from trialink.structuring import draft_criteria

P = "adm"
# Not prefixed "adm_" so clearing the form state keeps the admin logged in; the page timeout still clears it.
AUTH = "admauth_ok"
page_setup("Správa studií", P)
ss = st.session_state


def _go_home() -> None:
    for prefix in ("wiz", "doc", "pat"):
        reset(prefix)
    st.switch_page("Home.py")


if brand_header("adm_nav_logo"):
    _go_home()
c_back, _ = st.columns([1, 5])
if c_back.button("← Zpět", key="adm_nav_back", width="stretch"):
    st.switch_page("Home.py")
st.title("🗂️ Správa (pro administrátory)")


@st.cache_resource
def _throttle() -> Throttle:
    return Throttle()


stored_hash = os.getenv("ADMIN_PASSWORD_HASH", "")
if not stored_hash:
    st.error("Správa studií je vypnutá – v .env chybí ADMIN_PASSWORD_HASH (vytvoříte: python -m trialink.auth).")
    st.stop()
if not ss.get(AUTH):
    wait = _throttle().locked_for()
    if wait:
        st.error(f"Příliš mnoho neúspěšných pokusů. Zkuste to znovu za {wait} s.")
        st.stop()
    with st.form("admauth_login", clear_on_submit=True):
        pw = st.text_input("Heslo správce", type="password")
        login = st.form_submit_button("Přihlásit")
    if login:
        if verify_password(pw, stored_hash):
            _throttle().reset()
            ss[AUTH] = True
            st.rerun()
        _throttle().fail()
        time.sleep(1)
        st.error("Nesprávné heslo.")
    st.stop()
if st.button("🔒 Odhlásit správce"):
    ss.pop(AUTH, None)
    st.rerun()

st.caption("Referenční data (bez pacientských údajů). Nové a změněné studie se ukládají do IRIS jako nová verze; "
           "předchozí verze zůstávají v historii. Kritéria navržená AI mají stav **návrh** a musí je zkontrolovat onkolog.")

iris_ready = bool(os.getenv("IRIS_HOST") and os.getenv("IRIS_PASSWORD"))
if not iris_ready:
    st.error("IRIS není nakonfigurováno (IRIS_HOST / IRIS_PASSWORD v .env) – ukládání není možné.")


def _db():
    from trialink import db
    conn = db.connect()
    db.ensure_schema(conn)
    return db, conn


def _diff(studies):
    db, conn = _db()
    try:
        active = db.active_versions(conn)
    finally:
        conn.close()
    rows = []
    for s in studies:
        cur = active.get(s.study_id)
        state = "nová" if cur is None else ("beze změny" if cur[1] == db.version_hash(s) else "změněná")
        if cur and s.review == "nestrukturovano" and cur[2] != "nestrukturovano" and state == "změněná":
            state = "beze změny textu (kritéria se zachovají)"
        rows.append({"ID": s.study_id, "Zkratka": s.acronym, "Indikace": s.indication, "Stav": state,
                     "Kritéria": s.review, "Verze v IRIS": cur[0] if cur else "-"})
    return rows


def _show_study(s):
    st.markdown(f"**{s.acronym}** · {s.study_id} · {s.indication}")
    for c in s.contacts:
        st.caption(f"{c.role}: {c.name or '?'} <{c.email or '?'}>")
    for b in s.referral_blockers:
        st.warning(b)
    for w in s.warnings:
        st.caption(f"Varování: {w}")


tab_export, tab_manual = st.tabs(["📥 Nahrát export SONIS", "✍️ Přidat studii ručně"])

with tab_export:
    up = st.file_uploader("Export studií s otevřeným náborem (.csv, oddělovač TAB)", type=["csv", "tsv", "txt"], key=f"{P}_export")
    if up:
        try:
            studies, report, issues = load_catalog(data=up.getvalue(), name=up.name)
        except ValueError as exc:
            st.error(f"Soubor nelze načíst: {exc}")
            studies = None
        if studies is not None:
            st.success(f"Načteno {report.records} studií · opraveno {len(report.repaired)} · karanténa {len(report.quarantined)} "
                       f"· duplicity {len(report.duplicates)}")
            for q in report.quarantined:
                st.error(f"Karanténa – řádek {q['line']}: {q['reason']}")
            errors = [i for i in issues if "citace" in i or "neznám" in i]
            for e in errors:
                st.error(e)
            if iris_ready:
                rows = _diff(studies)
                st.dataframe(rows, hide_index=True, width="stretch")
                todo = [s for s in studies if s.review == "nestrukturovano"]
                use_llm = st.checkbox(f"Navrhnout kritéria pomocí AI pro studie bez pravidel ({len(todo)})", value=bool(todo),
                                      disabled=not todo, key=f"{P}_llm_export")
                if st.button("💾 Uložit do IRIS", type="primary", disabled=bool(errors)):
                    if use_llm:
                        bar = st.progress(0.0, "Navrhuji kritéria…")
                        for i, s in enumerate(todo, 1):
                            try:
                                d = draft_criteria(s.record)
                                if d.cohorts:
                                    s.cohorts, s.review = d.cohorts, "navrh"
                            except Exception as exc:
                                st.warning(f"{s.study_id}: návrh kritérií selhal ({type(exc).__name__}) – uloženo bez pravidel")
                            bar.progress(i / len(todo), f"{s.study_id} ({i}/{len(todo)})")
                    db, conn = _db()
                    try:
                        res = db.save_catalog(conn, studies, report, issues)
                    finally:
                        conn.close()
                    get_catalog.clear()
                    st.success(f"Uloženo: nových verzí {res['new_versions']}, beze změny {res['unchanged']}. "
                               "Lékařská i pacientská stránka už pracují s aktualizovaným katalogem.")

with tab_manual:
    st.markdown("Vyplňte údaje studie tak, jak jsou v SONIS. Povinné: ID, zkratka, vstupní kritéria.")
    with st.form(f"{P}_manual"):
        c1, c2, c3 = st.columns(3)
        sid = c1.text_input("ID projektu *", placeholder="2026/12")
        protocol = c2.text_input("Číslo protokolu")
        acronym = c3.text_input("Zkratka *")
        c1, c2 = st.columns(2)
        dg = c1.text_input("Přiřazené diagnózy (MKN-10)", placeholder="C180-C20 nebo C34")
        indication = c2.text_input("Indikace", placeholder="kolorektum KRAS G12C, 1. linie")
        c1, c2, c3, c4 = st.columns(4)
        pi = c1.text_input("Hlavní zkoušející (PI)")
        pi_mail = c2.text_input("E-mail PI")
        sc = c3.text_input("Studijní koordinátor (SC)", help="Více osob oddělte čárkou ve stejném pořadí jako e-maily")
        sc_mail = c4.text_input("E-mail SC")
        desc = st.text_area("Popis studie", height=100)
        inc = st.text_area("Vstupní kritéria *", height=200)
        exc = st.text_area("Vylučující kritéria", height=160)
        arm1 = st.text_area("Rameno 1", height=68)
        arm2 = st.text_area("Rameno 2", height=68)
        submitted = st.form_submit_button("🤖 Navrhnout kritéria pomocí AI")
        manual_only = st.form_submit_button("Pokračovat bez AI")
    if submitted or manual_only:
        try:
            rec = sonis.record_from_fields({
                "id_projektu": sid.strip(), "cislo_protokolu": protocol, "zkratka": acronym, "prirazene_diagnozy": dg,
                "indikace": indication, "hlavni_zkousejici": pi, "hlavni_zkousejici_mail": pi_mail, "kontaktni_osoba": sc,
                "kontaktni_osoba_mail": sc_mail, "popis_text": desc, "kriteria_vstupni_text": inc,
                "kriteria_vylucujici_text": exc, "rameno_1_text": arm1, "rameno_2_text": arm2,
            })
            ss[f"{P}_record"] = rec
            ss[f"{P}_rejected"] = []
            if submitted:
                with st.spinner("AI navrhuje kritéria…"):
                    d = draft_criteria(rec)
                ss[f"{P}_cohorts_json"] = json.dumps(d.cohorts, ensure_ascii=False, indent=2)
                ss[f"{P}_rejected"] = d.rejected
            else:
                ss[f"{P}_cohorts_json"] = json.dumps(study_from_record(rec).cohorts, ensure_ascii=False, indent=2)
        except ValueError as exc:
            st.error(str(exc))
        except Exception as exc:
            st.error(f"Návrh kritérií selhal ({type(exc).__name__}).")

    rec = ss.get(f"{P}_record")
    if rec:
        st.divider()
        if ss.get(f"{P}_rejected"):
            with st.expander(f"AI návrhy zamítnuté validací ({len(ss[f'{P}_rejected'])})"):
                for r in ss[f"{P}_rejected"]:
                    st.caption(r)
        st.markdown("#### Kritéria (JSON, lze upravit)")
        st.caption("Gramatika: pole diagnosis, histology, disease_extent, setting, prior_palliative_lines, her2_status, biomarker, "
                   "biomarker_negative, biomarker_value, prior_therapy, ecog, age, sex, can_swallow, manual, any_of. "
                   "Každé kritérium musí mít doslovnou citaci (quote) z textu studie.")
        text = st.text_area("Kohorty a kritéria", key=f"{P}_cohorts_json", height=360, label_visibility="collapsed")
        study = None
        try:
            cohorts = json.loads(text)
            study = study_from_record(rec, cohorts, "navrh") if cohorts else study_from_record(rec)
            problems = validate([study])
        except (json.JSONDecodeError, TypeError, KeyError, AttributeError) as exc:
            problems = [f"Neplatný JSON kritérií: {exc}"]
        if study:
            _show_study(study)
        for p in problems:
            st.error(p)
        c1, c2 = st.columns(2)
        if c1.button("💾 Uložit studii do IRIS", type="primary", disabled=bool(problems) or not iris_ready or study is None):
            db, conn = _db()
            try:
                res = db.save_study(conn, study)
            finally:
                conn.close()
            get_catalog.clear()
            for k in [k for k in ss if k.startswith(f"{P}_")]:
                del ss[k]
            st.success(f"Studie {study.acronym} uložena ({'nová verze' if res['new_versions'] else 'beze změny'}). "
                       "Je dostupná v párování.")
        if c2.button("Zrušit"):
            for k in [k for k in ss if k.startswith(f"{P}_")]:
                del ss[k]
            st.rerun()

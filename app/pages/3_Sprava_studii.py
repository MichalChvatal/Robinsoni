import csv
import io
import json
import os
import re
import time

import streamlit as st

from common import brand_header, get_catalog, page_setup, reset
from trialink import sonis
from trialink.auth import Throttle, verify_password
from trialink.catalog import Study, load_catalog, study_from_record, validate
from trialink.dictionaries import ICD_NAMES
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


def classify_domain(s: Study) -> str:
    """Classify study domain using the official ICD_NAMES dictionary and indication keywords."""
    raw = (s.diagnoses_raw or "").upper()
    codes = re.findall(r"C\d{2}", raw)
    names = list(dict.fromkeys(ICD_NAMES[c] for c in codes if c in ICD_NAMES))
    if len(names) == 1:
        return names[0]
    elif 1 < len(names) <= 2:
        return ", ".join(names)
    elif len(names) > 2:
        return "Vícečetné diagnózy (solidní nádory)"

    ind = (s.indication or "").lower()
    acr = (s.acronym or "").lower()
    text = f"{ind} {acr}"
    if any(k in text for k in ("solidní", "solidnich", "fáze i", "faze i", "vícečet", "vicečet")):
        return "Fáze I / Solidní nádory"
    if "prs" in text or "mammar" in text:
        return "Prs"
    if "plíc" in text or "nsclc" in text:
        return "Průdušky a plíce"
    if any(k in text for k in ("kolorekt", "střev", "crc")):
        return "Tlusté střevo a konečník"
    if "melanom" in text:
        return "Melanom kůže"
    return "Onkologie (obecná)"


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
        rows.append({"ID": s.study_id, "Zkratka": s.acronym, "Obor": classify_domain(s), "Indikace": s.indication, "Stav": state,
                     "Kritéria": s.review, "Verze v IRIS": cur[0] if cur else "-"})
    return rows


def _to_csv(study_list: list[Study]) -> bytes:
    buf = io.StringIO()
    writer = csv.writer(buf, delimiter=";")
    writer.writerow(["ID", "Zkratka", "Obor", "Indikace", "Diagnozy", "Protokol", "Stav_pravidel", "PI_jmeno", "PI_email", "SC_jmeno", "SC_email", "Pocet_kohort"])
    for s in study_list:
        pi = next((c for c in s.contacts if c.role == "PI"), None)
        sc = next((c for c in s.contacts if c.role == "SC"), None)
        writer.writerow([
            s.study_id,
            s.acronym,
            classify_domain(s),
            s.indication,
            s.diagnoses_raw,
            s.protocol,
            s.review,
            pi.name if pi else "",
            pi.email if pi else "",
            sc.name if sc else "",
            sc.email if sc else "",
            len(s.cohorts),
        ])
    return buf.getvalue().encode("utf-8-sig")


def _show_study(s: Study):
    st.markdown(f"**{s.acronym}** · {s.study_id} · {s.indication}")
    for c in s.contacts:
        st.caption(f"{c.role}: {c.name or '?'} <{c.email or '?'}>")
    for b in s.referral_blockers:
        st.warning(b)
    for w in s.warnings:
        st.caption(f"Varování: {w}")


def _show_study_detail(s: Study):
    with st.container(border=True):
        c1, c2, c3 = st.columns([2, 1, 1])
        c1.markdown(f"### 📋 {s.acronym} · `{s.study_id}`")
        c1.caption(f"**Indikace:** {s.indication}")
        c2.markdown(f"**Obor / Zaměření:**\n\n`{classify_domain(s)}`")
        status_badge = {"strukturovano": "✅ Ověřená pravidla", "navrh": "🤖 Návrh pravidel (AI)", "nestrukturovano": "⚪ Bez pravidel"}.get(s.review, s.review)
        c3.markdown(f"**Stav:**\n\n{status_badge}")

        st.divider()
        cc1, cc2 = st.columns(2)
        with cc1:
            st.markdown("**👩‍⚕️ Kontakty studie:**")
            if s.contacts:
                for c in s.contacts:
                    ext = f" (kl. {c.extension}{', simulovaná' if c.extension_simulated else ''})" if c.extension else ""
                    mail_str = f"[{c.email}](mailto:{c.email})" if c.email else "❓ chybí e-mail"
                    name_str = f"**{c.name}**" if c.name else "❓ chybí jméno"
                    st.markdown(f"- **{c.role}:** {name_str} · {mail_str}{ext}")
            else:
                st.info("Žádné kontakty nebyly přiřazeny.")
            for b in s.referral_blockers:
                st.warning(f"⚠️ {b}")
        with cc2:
            st.markdown("**🎯 Diagnózy a kódování:**")
            st.markdown(f"- **MKN-10 kódy:** `{s.diagnoses_raw or 'neuvedeno'}`")
            st.markdown(f"- **Číslo protokolu:** `{s.protocol or 'neuvedeno'}`")
            st.markdown(f"- **Počet kohort:** {len(s.cohorts)}")
            if s.warnings:
                for w in s.warnings:
                    st.caption(f"Poznámka: {w}")

        st.divider()
        st.markdown(f"#### 🧬 Strukturované kohorty a kritéria ({len(s.cohorts)})")
        if not s.cohorts:
            st.info("Tato studie zatím nemá definována strukturovaná kritéria.")
        for cohort in s.cohorts:
            with st.expander(f"Kohorta: **{cohort.get('label', cohort.get('id', 'Hlavní'))}** ({len(cohort.get('criteria', []))} pravidel)", expanded=True):
                criteria = cohort.get("criteria", [])
                if criteria:
                    for crit in criteria:
                        kind_icon = "🟢 VSTUPNÍ" if crit.get("kind") == "inclusion" else "🔴 VYLUČUJÍCÍ"
                        field_name = crit.get("field", "")
                        op = crit.get("op", "")
                        val = crit.get("value", "")
                        quote = crit.get("quote", "")

                        st.markdown(f"- **{kind_icon}** `{field_name}` (`{op}` `{val}`)")
                        if quote:
                            st.caption(f"&nbsp;&nbsp;&nbsp;&nbsp;📖 *Citace:* „{quote}“")
                else:
                    st.caption("Žádná kritéria v této kohortě.")

        with st.expander("📄 Původní texty ze SONIS (popis a kritéria)"):
            rec_f = s.record.fields
            if rec_f.get("popis_text"):
                st.markdown("**Popis studie:**")
                st.text(rec_f.get("popis_text"))
            if rec_f.get("kriteria_vstupni_text"):
                st.markdown("**Vstupní kritéria:**")
                st.text(rec_f.get("kriteria_vstupni_text"))
            if rec_f.get("kriteria_vylucujici_text"):
                st.markdown("**Vylučující kritéria:**")
                st.text(rec_f.get("kriteria_vylucujici_text"))
            for arm_idx in range(1, 5):
                arm_text = rec_f.get(f"rameno_{arm_idx}_text")
                if arm_text:
                    st.markdown(f"**Léčebné rameno {arm_idx}:**")
                    st.text(arm_text)


tab_active, tab_export, tab_manual = st.tabs(["📚 Studie v databázi", "📥 Nahrát export SONIS", "✍️ Přidat studii ručně"])

with tab_active:
    cat_studies, cat_source, cat_issues = get_catalog()

    active_versions = {}
    if iris_ready:
        try:
            db, conn = _db()
            try:
                active_versions = db.active_versions(conn)
            finally:
                conn.close()
        except Exception:
            active_versions = {}

    st.markdown("### 📋 Přehled klinických studií v databázi")
    st.caption(f"Aktuální zdroj dat: **{cat_source}**" + (f" · zjištěné neshody v datech: {len(cat_issues)}" if cat_issues else ""))

    # Metriky
    m1, m2, m3, m4 = st.columns(4)
    m1.metric("Celkem studií v katalogu", len(cat_studies), help="Všechny otevřené studie dostupné v systému")
    n_struct = sum(1 for s in cat_studies if s.review == "strukturovano")
    n_draft = sum(1 for s in cat_studies if s.review == "navrh")
    n_unstruct = sum(1 for s in cat_studies if s.review == "nestrukturovano")
    m2.metric("Ověřená pravidla", n_struct, help="Kritéria plně schválená onkologem")
    m3.metric("Návrhy pravidel (AI)", n_draft, help="Strukturovaná kritéria navržená AI, čekající na kontrolu")
    m4.metric("Bez pravidel", n_unstruct, help="Studie s nestrukturovaným textem")

    st.write("")

    # Filtry
    all_domains = sorted({classify_domain(s) for s in cat_studies})
    f_c1, f_c2, f_c3 = st.columns([2, 1.4, 1.4])
    q = f_c1.text_input("🔍 Hledat v databázi", placeholder="Zkratka, ID, indikace, diagnóza, biomarker, lékař...", key=f"{P}_cat_q")
    f_dom = f_c2.multiselect("Obor / Zaměření", options=all_domains, placeholder="Všechny obory", key=f"{P}_cat_dom")
    review_map = {"strukturovano": "✅ Ověřeno", "navrh": "🤖 Návrh AI", "nestrukturovano": "⚪ Bez pravidel"}
    f_rev = f_c3.multiselect("Stav pravidel", options=list(review_map), format_func=review_map.get, placeholder="Všechny stavy", key=f"{P}_cat_rev")

    def _matches(s: Study) -> bool:
        dom = classify_domain(s)
        if f_dom and dom not in f_dom:
            return False
        if f_rev and s.review not in f_rev:
            return False
        if q:
            term = q.strip().lower()
            pi = next((c.name for c in s.contacts if c.role == "PI"), "")
            sc = next((c.name for c in s.contacts if c.role == "SC"), "")
            haystack = f"{s.study_id} {s.acronym} {s.protocol} {s.indication} {s.diagnoses_raw} {dom} {pi} {sc}".lower()
            if term not in haystack:
                return False
        return True

    filtered = [s for s in cat_studies if _matches(s)]

    st.write(f"Zobrazeno **{len(filtered)}** z **{len(cat_studies)}** studií:")

    table_data = []
    for s in filtered:
        dom = classify_domain(s)
        pi = next((c for c in s.contacts if c.role == "PI"), None)
        sc = next((c for c in s.contacts if c.role == "SC"), None)
        pi_mail = f"mailto:{pi.email}" if pi and pi.email else None
        sc_mail = f"mailto:{sc.email}" if sc and sc.email else None
        table_data.append({
            "ID": s.study_id,
            "Zkratka": s.acronym,
            "Diagnózy": s.diagnoses_raw or "—",
            "Obor": dom,
            "Indikace": s.indication,
            "Hlavní zkoušející (PI)": pi.name if pi and pi.name else "—",
            "E-mail PI": pi_mail,
            "Koordinátor (SC)": sc.name if sc and sc.name else "—",
            "E-mail SC": sc_mail,
            "Kohorty": len(s.cohorts),
            "Kontakty": "⚠️ Chybí kontakt" if s.referral_blockers else "✅ OK",
        })

    st.dataframe(
        table_data,
        hide_index=True,
        width="stretch",
        column_config={
            "ID": st.column_config.TextColumn("ID projektu", width="small"),
            "Zkratka": st.column_config.TextColumn("Zkratka", width="small"),
            "Diagnózy": st.column_config.TextColumn("MKN-10", width="small"),
            "Obor": st.column_config.TextColumn("Obor / Zaměření", width="medium"),
            "Indikace": st.column_config.TextColumn("Indikace", width="large"),
            "Hlavní zkoušející (PI)": st.column_config.TextColumn("Hlavní zkoušející (PI)", width="medium"),
            "E-mail PI": st.column_config.LinkColumn("E-mail PI", display_text=r"mailto:(.*)", width="medium"),
            "Koordinátor (SC)": st.column_config.TextColumn("Koordinátor (SC)", width="medium"),
            "E-mail SC": st.column_config.LinkColumn("E-mail SC", display_text=r"mailto:(.*)", width="medium"),
            "Kohorty": st.column_config.NumberColumn("Kohorty", width="small"),
            "Kontakty": st.column_config.TextColumn("Kontakty", width="small"),
        },
    )

    c_exp1, _ = st.columns([1, 3])
    csv_bytes = _to_csv(filtered)
    c_exp1.download_button("📥 Exportovat zobrazené (CSV)", data=csv_bytes, file_name="studie_databaze.csv", mime="text/csv", key=f"{P}_dl_csv")

    st.divider()
    st.markdown("#### 🔎 Podrobný detail a pravidla studie")
    study_map = {s.study_id: f"{s.acronym} ({s.study_id}) · {s.indication}" for s in filtered}
    sel_id = st.selectbox(
        "Vyberte studii pro zobrazení podrobných pravidel, kontaktů a textu",
        options=[None, *study_map.keys()],
        format_func=lambda x: "— vyberte studii pro podrobný náhled —" if x is None else study_map[x],
        key=f"{P}_inspect_study",
    )
    if sel_id:
        target = next(s for s in filtered if s.study_id == sel_id)
        _show_study_detail(target)

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

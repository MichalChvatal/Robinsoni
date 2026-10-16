import time
from datetime import date

import streamlit as st

from common import apply_profile, disclaimer, get_catalog, page_setup, profile_form, render_result, reset
from trialink.demo_cases import CASES
from trialink.extraction import ExtractionError, extract_profile
from trialink.matching import STATUS_CANDIDATE, STATUS_EXCLUDED, STATUS_VERIFY, match
from trialink.referral import build_referral, simulate_send

P = "doc"
page_setup("Lékař", P)
studies, source, _ = get_catalog()
ss = st.session_state
ss.setdefault(f"{P}_t0", time.time())

st.title("👩‍⚕️ TriaLink – lékař")
st.caption(f"{len(studies)} studií · zdroj: {source}")

with st.sidebar:
    st.markdown("### Rychlý start")
    case = st.selectbox("Testovací kazuistika", [None, *CASES], format_func=lambda v: "— vybrat —" if v is None else v)
    if st.button("Načíst kazuistiku", disabled=case is None, width="stretch"):
        reset(P)
        apply_profile(P, CASES[case]())
        ss[f"{P}_t0"] = time.time()
        st.rerun()
    if st.button("🗑️ Vymazat údaje pacienta", width="stretch"):
        reset(P)
        st.rerun()
    st.caption("Údaje existují jen v paměti této relace.")

with st.expander("📄 Vyplnit ze zprávy (AI extrakce – OpenAI, cloud)"):
    st.warning("Text bude odeslán do cloudové služby OpenAI. Vkládejte pouze anonymizovanou zprávu. Text se neukládá.")
    with st.form(f"{P}_extract", clear_on_submit=True):
        text = st.text_area("Anonymizovaná zpráva", height=180, max_chars=40_000)
        upload = st.file_uploader("…nebo nahrajte .txt", type=["txt"])
        go = st.form_submit_button("Extrahovat parametry")
    if go:
        report = upload.getvalue().decode("utf-8", errors="replace") if upload else text
        try:
            with st.spinner("Extrahuji…"):
                result = extract_profile(report)
            reset(P)
            apply_profile(P, result.profile)
            ss[f"{P}_t0"] = time.time()
            ss[f"{P}_dropped"] = result.dropped
            st.rerun()
        except ExtractionError as exc:
            st.error(str(exc))
        finally:
            report = text = upload = None
    if ss.get(f"{P}_dropped"):
        st.info("Vynecháno (údaj nebyl doložen citací ze zprávy): " + ", ".join(ss[f"{P}_dropped"]))

profile = profile_form(P)

st.divider()
results = match(studies, profile)
groups = {s: [r for r in results if r.status == s] for s in (STATUS_CANDIDATE, STATUS_VERIFY, STATUS_EXCLUDED)}
targeted_verify = [r for r in groups[STATUS_VERIFY] if r.targeted]
other_verify = [r for r in groups[STATUS_VERIFY] if not r.targeted]

st.markdown("### Výsledky")
tabs = st.tabs([f"🟢 Kandidáti ({len(groups[STATUS_CANDIDATE])})", f"🟡 K ověření ({len(targeted_verify)})",
                f"🟡 Napříč diagnózami ({len(other_verify)})", f"🔴 Vyloučené ({len(groups[STATUS_EXCLUDED])})"])


def result_list(items, allow_referral: bool):
    if not items:
        st.info("Žádná studie v této skupině.")
    for r in items:
        with st.container(border=True):
            render_result(r)
            if allow_referral and st.button("✉️ Připravit referral", key=f"{P}_ref_{r.study.study_id}"):
                ss[f"{P}_referral"] = r.study.study_id


with tabs[0]:
    result_list(groups[STATUS_CANDIDATE], True)
with tabs[1]:
    result_list(targeted_verify, True)
with tabs[2]:
    result_list(other_verify, True)
with tabs[3]:
    result_list(groups[STATUS_EXCLUDED], False)

chosen = ss.get(f"{P}_referral")
selected = next((r for r in results if r.study.study_id == chosen), None)
if selected:
    st.divider()
    st.markdown(f"### ✉️ Referral – {selected.study.acronym}")
    ref = build_referral(selected, profile)
    for c in selected.study.contacts:
        ext = f" · kl. {c.extension}{' (simulováno)' if c.extension_simulated else ''}" if c.extension else ""
        st.markdown(f"- **{c.role}** {c.name or '❓ jméno chybí'} <{c.email or '❓ e-mail chybí'}>{ext}")
    if ref.blockers:
        st.error("Odeslání blokováno: " + "; ".join(ref.blockers))
    st.text_input("Předmět", ref.subject, disabled=True)
    st.text_area("Text zprávy", ref.body, height=360, disabled=True)
    st.caption("Zpráva neobsahuje identifikační údaje pacienta. Odeslání je v prototypu pouze simulované.")
    if st.button("Odeslat (simulace)", type="primary", disabled=bool(ref.blockers)):
        st.success(simulate_send(ref))
        st.metric("Čas od načtení případu do odeslání referral", f"{time.time() - ss[f'{P}_t0']:.0f} s")
        ss.pop(f"{P}_referral", None)

disclaimer()
st.caption(f"Datum hodnocení: {date.today():%d. %m. %Y}")

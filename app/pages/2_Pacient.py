import streamlit as st

from common import apply_profile, disclaimer, get_catalog, page_setup, profile_form, render_result, reset
from trialink.extraction import ExtractionError, extract_profile
from trialink.lay_terms import TOOLTIPS
from trialink.matching import STATUS_CANDIDATE, STATUS_VERIFY, match

P = "pat"
page_setup("Pacient", P)
studies, _, _ = get_catalog()
ss = st.session_state

st.title("🙂 Průvodce klinickými studiemi")
st.write("Odpovězte na několik otázek o své nemoci. Pokud něco nevíte, zvolte „nevím“ – i tak vám ukážeme možnosti, "
         "které stojí za to probrat s vaším onkologem.")
st.info(f"**Co je klinická studie?** {TOOLTIPS['studie']}")

with st.sidebar:
    if st.button("🗑️ Vymazat moje údaje", width="stretch"):
        reset(P)
        st.rerun()
    st.caption("Nic neukládáme. Údaje se smažou tlačítkem výše nebo po 15 minutách nečinnosti.")

with st.expander("📄 Mám lékařskou zprávu – vyplnit automaticky"):
    st.warning("Zpráva bude zpracována cloudovou službou umělé inteligence (OpenAI). Před vložením z ní odstraňte jméno, "
               "rodné číslo, adresu a další osobní údaje. Text si neukládáme.")
    with st.form(f"{P}_extract", clear_on_submit=True):
        text = st.text_area("Vložte text zprávy", height=160, max_chars=40_000)
        upload = st.file_uploader("…nebo nahrajte textový soubor (.txt)", type=["txt"])
        go = st.form_submit_button("Vyplnit formulář ze zprávy")
    if go:
        report = upload.getvalue().decode("utf-8", errors="replace") if upload else text
        try:
            with st.spinner("Čtu zprávu…"):
                result = extract_profile(report)
            reset(P)
            apply_profile(P, result.profile)
            st.rerun()
        except ExtractionError as exc:
            st.error(str(exc))
        finally:
            report = text = upload = None

st.caption("Formulář si prosím zkontrolujte – automatické vyplnění může obsahovat chyby.")
profile = profile_form(P, lay=True)

st.divider()
st.markdown("### Studie, o kterých si můžete promluvit s lékařem")
results = [r for r in match(studies, profile) if r.status in (STATUS_CANDIDATE, STATUS_VERIFY)]
targeted = [r for r in results if r.targeted]
if not profile.icd10:
    st.info("Vyberte prosím diagnózu (krok 1) – pak vám ukážeme odpovídající studie.")
elif not targeted:
    st.info("Pro zadané údaje jsme nenašli studii zaměřenou na vaši diagnózu. Zeptejte se svého onkologa, "
            "zda by pro vás nebyla vhodná jiná studie.")
for r in targeted:
    with st.container(border=True):
        render_result(r, lay=True)
        sc = [c for c in r.study.contacts if c.role == "SC" and c.email]
        if sc:
            st.caption("Kontakt na studijní tým (doporučujeme nejprve probrat s ošetřujícím lékařem): "
                       + ", ".join(f"{c.name} <{c.email}>" for c in sc))
with st.expander(f"Studie napříč diagnózami ({len(results) - len(targeted)})"):
    st.caption("Tyto studie (často fáze I) nejsou zaměřené na konkrétní diagnózu. Jejich vhodnost může posoudit jen lékař.")
    for r in results:
        if not r.targeted:
            st.markdown(f"- **{r.study.acronym}** – {r.study.indication}")

disclaimer(lay=True)

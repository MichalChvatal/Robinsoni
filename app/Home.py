import streamlit as st

from common import disclaimer, get_catalog, page_setup

page_setup("Úvod", "home")

st.title("🔗 TriaLink")
st.subheader("Průvodce klinickými studiemi MOÚ")
studies, source, issues = get_catalog()
st.caption(f"Načteno {len(studies)} studií s otevřeným náborem · zdroj: {source} · export SONIS k 29. 9. 2026")

c1, c2 = st.columns(2)
with c1:
    st.markdown("### 👩‍⚕️ Pro lékaře")
    st.write("Rozhodovací strom (diagnóza → stadium → linie léčby → biomarkery), párování na úrovni variant a referral na PI a SC.")
    st.page_link("pages/1_Lekar.py", label="Otevřít rozhraní pro lékaře", icon="➡️")
with c2:
    st.markdown("### 🙂 Pro pacienty")
    st.write("Srozumitelný průvodce s nápovědou. Volitelně lze vložit anonymizovanou zprávu a parametry se vyplní automaticky.")
    st.page_link("pages/2_Pacient.py", label="Otevřít průvodce pro pacienty", icon="➡️")

st.divider()
st.markdown("**Ochrana dat:** aplikace neukládá žádné údaje o pacientech do databáze ani na disk. Zadané údaje existují jen "
            "v operační paměti po dobu relace a smažou se tlačítkem *Vymazat* nebo po 15 minutách nečinnosti.")
st.warning("Automatická extrakce ze zprávy v tomto prototypu používá cloudovou službu OpenAI (ne lokální LLM). "
           "Text zprávy je odeslán mimo nemocnici – vkládejte pouze anonymizované zprávy.")
disclaimer()

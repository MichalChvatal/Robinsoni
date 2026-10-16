import streamlit as st

import wizard
from common import get_catalog, page_setup

page_setup("Průvodce", "wiz", "doc", "pat")

st.title("🔗 TriaLink")
studies, source, issues = get_catalog()
st.caption(f"Průvodce klinickými studiemi MOÚ · {len(studies)} studií s otevřeným náborem · zdroj: {source}")

wizard.run(studies)

if st.session_state.get("wiz_step", "role") == "role":
    st.divider()
    st.page_link("pages/3_Sprava_studii.py", label="Správa (pro administrátory)", icon="🗂️")

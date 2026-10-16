import streamlit as st

import wizard
from common import brand_header, get_catalog, page_setup

page_setup("Průvodce", "wiz", "doc", "pat")

brand_header("wiz_home", on_home=wizard.go_home)
studies, source, issues = get_catalog()
st.caption(f"Průvodce klinickými studiemi MOÚ · {len(studies)} studií s otevřeným náborem · zdroj: {source}")

wizard.run(studies)

if st.session_state.get("wiz_step", "role") == "role":
    st.divider()
    st.page_link("pages/3_Sprava_studii.py", label="Správa (pro administrátory)", icon="🗂️")

import sys
from pathlib import Path

# Repo sources must win over a stale installed copy of `trialink` in site-packages (Streamlit Cloud).
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import streamlit as st  # noqa: E402

import wizard  # noqa: E402
from common import brand_header, get_catalog, page_setup  # noqa: E402

page_setup("Průvodce", "wiz", "doc", "pat")

brand_header("wiz_home", on_home=wizard.go_home)
studies, source, issues = get_catalog()


wizard.run(studies)

if st.session_state.get("wiz_step", "role") == "role":
    st.divider()
    st.page_link("pages/3_Sprava_studii.py", label="Správa (pro administrátory)", icon="🗂️")

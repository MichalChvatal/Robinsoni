import base64
from pathlib import Path

import streamlit as st

import wizard
from common import get_catalog, page_setup

page_setup("Průvodce", "wiz", "doc", "pat")

LOGO = Path(__file__).resolve().parent / "static" / "mou_logo.svg"
st.markdown("""<style>
.st-key-wiz_home button {padding: 0; min-height: 0;}
.st-key-wiz_home button p {font-size: 2.6rem; font-weight: 700; line-height: 1.2;}
</style>""", unsafe_allow_html=True)
c_logo, c_title = st.columns([1, 5], vertical_alignment="center")
if LOGO.is_file():
    logo = base64.b64encode(LOGO.read_bytes()).decode()
    c_logo.html(f'<a href="https://www.mou.cz" target="_blank" rel="noopener"><img src="data:image/svg+xml;base64,{logo}" '
                'alt="Masarykův onkologický ústav" style="height:64px;width:auto;background:#fff;'
                'padding:6px 10px;border-radius:8px"></a>')
c_title.button("TriaLink", key="wiz_home", type="tertiary", on_click=wizard.go_home, help="Zpět na úvod")
studies, source, issues = get_catalog()
st.caption(f"Průvodce klinickými studiemi MOÚ · {len(studies)} studií s otevřeným náborem · zdroj: {source}")

wizard.run(studies)

if st.session_state.get("wiz_step", "role") == "role":
    st.divider()
    st.page_link("pages/3_Sprava_studii.py", label="Správa (pro administrátory)", icon="🗂️")

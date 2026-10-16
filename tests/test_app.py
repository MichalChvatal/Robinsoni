from pathlib import Path

from conftest import requires_catalog

from streamlit.testing.v1 import AppTest

APP = Path(__file__).resolve().parent.parent / "app"


def _run(page: str) -> AppTest:
    at = AppTest.from_file(str(APP / page), default_timeout=60)
    at.run()
    assert not at.exception, at.exception
    return at


@requires_catalog
def test_home_renders():
    at = _run("Home.py")
    assert "TriaLink" in at.title[0].value


@requires_catalog
def test_doctor_flow_case2_to_simulated_referral():
    at = _run("pages/1_Lekar.py")
    at.sidebar.selectbox[0].select("Case 2 – skvamózní ca jícnu, po 1. linii").run()
    at.sidebar.button[0].click().run()
    assert not at.exception, at.exception
    text = " ".join(m.value for m in at.markdown)
    assert "MSD - jícen" in text
    ref_buttons = [b for b in at.button if b.key == "doc_ref_2025/R46"]
    assert ref_buttons
    ref_buttons[0].click().run()
    body = next(t for t in at.text_area if t.key == "doc_ref_body_2025/R46")
    assert "MSD - jícen" in body.value
    body.input(body.value + "\nDoplněk lékaře: prosím o rychlé posouzení.").run()
    send = [b for b in at.button if b.label == "Odeslat (simulace)"]
    assert send and not send[0].disabled
    send[0].click().run()
    assert any("SIMULACE" in s.value for s in at.success)
    assert any("Doplněk lékaře" in t.value for t in at.text)


@requires_catalog
def test_doctor_reset_clears_patient_state():
    at = _run("pages/1_Lekar.py")
    at.sidebar.selectbox[0].select("Case 1 – CRC, KRAS G13D, metastatický").run()
    at.sidebar.button[0].click().run()
    assert at.session_state["doc_icd"] == "C186"
    at.sidebar.button[1].click().run()
    assert "doc_icd" not in at.session_state or not at.session_state["doc_icd"]
    assert "doc_bio_init" not in at.session_state


@requires_catalog
def test_patient_page_renders():
    at = _run("pages/2_Pacient.py")
    assert at.title[0].value.startswith("🙂")

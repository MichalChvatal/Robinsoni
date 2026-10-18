import re
import sys
from functools import cache
from pathlib import Path

import pytest
from conftest import requires_catalog, requires_export

from streamlit.testing.v1 import AppTest
from streamlit.testing.v1.element_tree import Block

from trialink.catalog import load_catalog
from trialink.demo_cases import CASES
from trialink.matching import STATUS_CANDIDATE, STATUS_EXCLUDED, STATUS_VERIFY, evaluate_cohort, match
from trialink.model import normalize_variant

APP = Path(__file__).resolve().parent.parent / "app"
SUMMARY_LABELS = ("🟢 Odpovídá", "🟡 K ověření", "🟠 Studie pro více druhů nádorů")
GROUP_ICONS = {"cand": "🟢", "verify": "🟡", "other": "🟠"}


@pytest.fixture(autouse=True)
def _demo_cases(monkeypatch):
    monkeypatch.setenv("TRIALINK_DEMO", "1")


def _run(page: str = "Home.py") -> AppTest:
    at = AppTest.from_file(str(APP / page), default_timeout=60)
    at.run()
    assert not at.exception, at.exception
    return at


def _click(at: AppTest, key: str) -> AppTest:
    at.button(key=key).click().run()
    assert not at.exception, at.exception
    return at


def _doctor_with_case(case: str) -> AppTest:
    at = _run()
    _click(at, "wiz_role_doc")
    _click(at, "wiz_choice_report")
    at.selectbox(key="doc_demo").select(case).run()
    return _click(at, "wiz_demo")


@requires_catalog
def test_home_renders_role_choice():
    at = _run()
    assert "TriaLink" in at.button(key="wiz_home").label
    assert {b.key for b in at.button} >= {"wiz_role_doc", "wiz_role_pat"}


@requires_catalog
def test_doctor_choice_offers_quick_entry_first():
    at = _run()
    _click(at, "wiz_role_doc")
    keys = [b.key for b in at.button if b.key in ("wiz_choice_quick", "wiz_choice_form", "wiz_choice_report")]
    assert keys == ["wiz_choice_quick", "wiz_choice_form", "wiz_choice_report"]


@requires_catalog
def test_trialink_title_goes_home():
    at = _doctor_with_case("Case 1 – CRC, KRAS G13D, metastatický")
    _click(at, "wiz_home")
    assert "wiz_step" not in at.session_state and "doc_icd" not in at.session_state


@requires_catalog
def test_results_offer_pdf_export():
    at = _doctor_with_case("Case 1 – CRC, KRAS G13D, metastatický")
    _click(at, "wiz_search")
    assert at.get("download_button")


@requires_catalog
def test_doctor_flow_case2_to_simulated_referral():
    at = _doctor_with_case("Case 2 – skvamózní ca jícnu, po 1. linii")
    assert at.session_state["wiz_step"] == "detail"
    _click(at, "wiz_search")
    assert at.session_state["wiz_step"] == "results"
    detail = [e for e in at.expander if e.label.startswith("Detail kritérií")]
    assert detail and all(not e.proto.expanded for e in detail)
    text = " ".join(m.value for m in at.markdown)
    assert "MSD - jícen" in text
    _click(at, "doc_ref_2025/R46")
    body = at.text_area(key="doc_ref_body_2025/R46")
    assert "MSD - jícen" in body.value
    body.input(body.value + "\nDoplněk lékaře: prosím o rychlé posouzení.").run()
    send = at.button(key="doc_ref_send")
    assert not send.disabled
    send.click().run()
    assert any("SIMULACE" in s.value for s in at.success)
    assert any("Doplněk lékaře" in t.value for t in at.text)


@requires_catalog
def test_back_button_walks_history_and_keeps_form_values():
    at = _doctor_with_case("Case 1 – CRC, KRAS G13D, metastatický")
    _click(at, "wiz_search")
    _click(at, "wiz_back")
    assert at.session_state["wiz_step"] == "detail"
    assert at.session_state["doc_icd"] == "C186"
    assert at.session_state["doc_morpho"] == "81403"
    for expected in ("report", "doc_choice", "role"):
        _click(at, "wiz_back")
        assert at.session_state["wiz_step"] == expected


@requires_catalog
def test_restart_clears_patient_state():
    at = _doctor_with_case("Case 1 – CRC, KRAS G13D, metastatický")
    assert at.session_state["doc_icd"] == "C186"
    _click(at, "wiz_restart")
    assert "doc_icd" not in at.session_state
    assert "doc_bio_init" not in at.session_state
    assert "wiz_role_doc" in {b.key for b in at.button}


@requires_catalog
def test_patient_without_report_goes_to_lay_form():
    at = _run()
    _click(at, "wiz_role_pat")
    assert at.session_state["wiz_step"] == "report"
    _click(at, "wiz_no_report")
    assert at.session_state["wiz_step"] == "detail"
    assert any(s.label.startswith("Kde nádor vznikl") for s in at.selectbox)


# --- quick entry (doctor "⚡ Rychlé zadání") ---------------------------------------------------------------------

def _quick() -> AppTest:
    at = _run()
    _click(at, "wiz_role_doc")
    return _click(at, "wiz_choice_quick")


def _active(at: AppTest) -> str:
    return at.session_state["doc_quick_field"]


def _qkey(at: AppTest, field: str) -> str:
    return f"doc_q_{field}_{at.session_state['doc_quick_rev']}"


def _quick_select(at: AppTest, field: str, value) -> AppTest:
    at.selectbox(key=_qkey(at, field)).select(value).run()
    assert not at.exception, at.exception
    return at


def _unknown_until(at: AppTest, field: str) -> AppTest:
    for _ in range(len(("icd", "morpho", "histology", "extent", "lines", "therapies", "her2", "biomarkers"))):
        if _active(at) == field:
            return at
        _click(at, "doc_q_unknown")
    assert _active(at) == field
    return at


@requires_catalog
def test_quick_card_opens_quick_row():
    at = _quick()
    assert at.session_state["wiz_step"] == "quick"
    assert _active(at) == "icd"
    assert at.selectbox(key=_qkey(at, "icd")).value is None
    assert {b.key for b in at.button} >= {"doc_q_unknown", "doc_q_prev", "wiz_quick_full", "wiz_search"}


@requires_catalog
def test_quick_auto_advance_unknown_and_chip():
    at = _quick()
    _quick_select(at, "icd", "C186")
    assert at.session_state["doc_quick"]["icd"] == "C186"
    assert _active(at) == "morpho"
    assert "icd" in at.session_state["doc_quick_answered"]
    _click(at, "doc_q_unknown")
    assert "morpho" in at.session_state["doc_quick_answered"]
    assert at.session_state["doc_quick"]["morpho"] is None
    assert _active(at) == "histology"
    _click(at, "doc_qchip_icd")
    assert _active(at) == "icd"
    assert at.selectbox(key=_qkey(at, "icd")).value == "C186"
    assert at.session_state["doc_quick"]["icd"] == "C186"


@requires_catalog
def test_quick_search_edit_full_form_and_back():
    at = _quick()
    _quick_select(at, "icd", "C186")
    _click(at, "wiz_search")
    assert at.session_state["wiz_step"] == "results"
    assert at.session_state["doc_profile"].icd10 == "C186"
    assert not at.tabs
    assert any(all(label in m.value for label in SUMMARY_LABELS) for m in at.markdown)
    _click(at, "wiz_edit")
    assert at.session_state["wiz_step"] == "quick"
    assert at.session_state["doc_quick"]["icd"] == "C186"
    _click(at, "wiz_quick_full")
    assert at.session_state["wiz_step"] == "detail"
    assert at.session_state["doc_icd"] == "C186"
    _click(at, "wiz_to_quick")
    assert at.session_state["wiz_step"] == "quick"
    assert at.session_state["doc_quick"]["icd"] == "C186"
    _click(at, "wiz_restart")
    assert not [k for k in at.session_state if k.startswith("doc_quick")]
    assert "wiz_role_doc" in {b.key for b in at.button}


@requires_catalog
def test_quick_from_demo_case_keeps_values():
    at = _doctor_with_case("Case 1 – CRC, KRAS G13D, metastatický")
    _click(at, "wiz_to_quick")
    quick = at.session_state["doc_quick"]
    assert (quick["icd"], quick["morpho"], quick["lines"]) == ("C186", "81403", 0)
    assert _active(at) == "her2"
    _click(at, "wiz_search")
    assert at.session_state["doc_profile"].biomarkers["KRAS"].variant == "G13D"
    _click(at, "wiz_edit")
    assert at.session_state["wiz_step"] == "quick"


@requires_catalog
def test_patient_has_no_quick_entry():
    at = _run()
    _click(at, "wiz_role_pat")
    assert "wiz_choice_quick" not in {b.key for b in at.button}
    _click(at, "wiz_no_report")
    assert at.session_state["wiz_step"] == "detail"
    assert not {"wiz_choice_quick", "wiz_to_quick"} & {b.key for b in at.button}


@requires_catalog
def test_quick_new_diagnosis_drops_disallowed_morphology(monkeypatch):
    monkeypatch.syspath_prepend(str(APP))
    import common
    index, _ = common.get_topo_morpho()
    if index is None:
        pytest.skip("číselník topografie × morfologie není k dispozici")
    morph = next(m for m in index.morphologies("C186") if m not in index.morphologies("C61"))
    at = _quick()
    _quick_select(at, "icd", "C186")
    _quick_select(at, "morpho", morph)
    assert at.session_state["doc_quick"]["morpho"] == morph
    _click(at, "doc_qchip_icd")
    _quick_select(at, "icd", "C61")
    quick = at.session_state["doc_quick"]
    assert quick["icd"] == "C61" and quick["morpho"] is None
    assert "morpho" not in at.session_state["doc_quick_answered"]
    assert _active(at) == "morpho"
    assert any("není pro novou diagnózu" in c.value for c in at.caption)
    assert quick["sex"] == "M"


@requires_export
def test_quick_biomarker_variant_is_exact():
    at = _quick()
    _quick_select(at, "icd", "C186")
    _unknown_until(at, "biomarkers")
    rev = at.session_state["doc_quick_rev"]
    at.selectbox(key=f"doc_q_bio_marker_{rev}").select("KRAS")
    at.text_input(key=f"doc_q_bio_detail_{rev}").input("p.(Gly12Cys)").run()
    assert not at.exception, at.exception
    kras = at.session_state["doc_quick"]["biomarkers"]["KRAS"]
    assert kras.status == "pozitivni"
    assert normalize_variant(kras.variant) == "G12C"
    assert any(b.label.startswith("KRAS G12C") for b in at.button)
    _click(at, "doc_q_done")
    assert "biomarkers" in at.session_state["doc_quick_answered"]
    _click(at, "wiz_search")
    profile = at.session_state["doc_profile"]
    study = next(s for s in load_catalog()[0] if s.study_id == "2022/41")
    cohort = study.cohorts[0]
    kras_state = {r.criterion["id"]: r.state for r in evaluate_cohort(study, cohort, profile).results}["kras"]
    assert kras_state == "splneno"
    profile.biomarkers["KRAS"].variant = "G13D"
    kras_state = {r.criterion["id"]: r.state for r in evaluate_cohort(study, cohort, profile).results}["kras"]
    assert kras_state == "nesplneno"

CASE1 = "Case 1 – CRC, KRAS G13D, metastatický"


@requires_catalog
def test_quick_changes_reach_detail_after_generic_back():
    at = _doctor_with_case(CASE1)
    assert at.session_state["doc_ecog"] == 0
    _click(at, "wiz_to_quick")
    _click(at, "doc_qchip_ecog")
    at.segmented_control(key=_qkey(at, "ecog")).set_value(2).run()
    assert not at.exception, at.exception
    assert at.session_state["doc_quick"]["ecog"] == 2
    _click(at, "wiz_back")
    assert at.session_state["wiz_step"] == "detail"
    assert at.selectbox(key="doc_ecog").value == 2
    assert at.selectbox(key="doc_icd").value == "C186"
    _click(at, "wiz_search")
    assert at.session_state["doc_profile"].ecog == 2
    assert at.session_state["doc_profile"].biomarkers["KRAS"].variant == "G13D"


@requires_catalog
def test_detail_changes_reach_quick_after_generic_back():
    at = _doctor_with_case(CASE1)
    _click(at, "wiz_to_quick")
    assert _active(at) == "her2"
    _click(at, "doc_q_unknown")
    _click(at, "wiz_quick_full")
    assert at.session_state["wiz_step"] == "detail"
    at.selectbox(key="doc_ecog").select(3).run()
    assert not at.exception, at.exception
    _click(at, "wiz_back")
    assert at.session_state["wiz_step"] == "quick"
    assert at.session_state["doc_quick"]["ecog"] == 3
    assert at.button(key="doc_qchip_ecog").label == "ECOG 3"
    assert "her2" in at.session_state["doc_quick_answered"]  # "Nevím" is not asked again
    _click(at, "wiz_search")
    assert at.session_state["doc_profile"].ecog == 3


@requires_catalog
def test_detail_reruns_keep_data_editors():
    at = _doctor_with_case(CASE1)
    rev = at.session_state["doc_editor_rev"]
    at.selectbox(key="doc_ecog").select(2).run()
    at.run()
    assert at.session_state["doc_editor_rev"] == rev
    at = _quick()  # profile without extent: the "missing widget" check must not re-apply on every rerun
    _quick_select(at, "icd", "C186")
    _click(at, "wiz_quick_full")
    rev = at.session_state["doc_editor_rev"]
    at.selectbox(key="doc_ecog").select(1).run()
    at.run()
    assert not at.exception, at.exception
    assert at.session_state["doc_editor_rev"] == rev
    assert at.selectbox(key="doc_ecog").value == 1


@requires_catalog
def test_quick_sex_conflicting_with_new_diagnosis_is_editable():
    at = _quick()
    _quick_select(at, "icd", "C186")
    for _ in range(12):
        if _active(at) == "sex":
            break
        _click(at, "doc_q_unknown")
    assert _active(at) == "sex"
    at.segmented_control(key=_qkey(at, "sex")).set_value("F").run()
    _click(at, "doc_qchip_icd")
    _quick_select(at, "icd", "C61")
    assert at.session_state["doc_quick"]["sex"] == "F"
    assert any("týká pouze pohlaví" in c.value for c in at.caption)
    _click(at, "doc_qchip_sex")
    assert _active(at) == "sex"
    at.segmented_control(key=_qkey(at, "sex")).set_value("M").run()
    assert not at.exception, at.exception
    assert at.session_state["doc_quick"]["sex"] == "M"
    assert _active(at) != "sex"
    assert not any("týká pouze pohlaví" in c.value for c in at.caption)
    _click(at, "wiz_search")
    assert at.session_state["doc_profile"].sex == "M"


# --- doctor results: one vertical list (🟢 cand → 🟡 verify → 🟠 other, excluded hidden) ---------------------------

CARD_KEY = re.compile(r"^doc_res_(cand|verify|other)_([A-Za-z0-9_-]+)_(\d+)$")


@cache
def _app_studies():
    """The same studies the app uses (IRIS or local export) – cached reference data, no patient data."""
    if str(APP) not in sys.path:
        sys.path.insert(0, str(APP))
    import common
    return common.get_catalog()[0]


def _walk(node):
    yield node
    children = getattr(node, "children", None) or {}
    for k in sorted(children):
        yield from _walk(children[k])


def _block_key(node) -> str | None:
    block_id = getattr(node.proto, "id", "") if isinstance(node, Block) and node.proto is not None else ""
    # Keyed containers get the id "$$ID-<hash>-<user key>".
    return block_id.split("-", 2)[2] if block_id.count("-") >= 2 else None


def _cards(at: AppTest) -> list[tuple[str, Block]]:
    return [(key, n) for n in _walk(at.main) if (key := _block_key(n)) and CARD_KEY.match(key)]


def _heading(card: Block) -> str:
    return next(n.value for n in _walk(card) if type(n).__name__ == "Markdown")


def _sanitize(study_id: str) -> str:
    return re.sub(r"[^A-Za-z0-9_-]", "_", study_id)


def _doctor_groups(at: AppTest) -> dict[str, list]:
    results = match(_app_studies(), at.session_state["doc_profile"])
    verify = [r for r in results if r.status == STATUS_VERIFY]
    return {
        "cand": [r for r in results if r.status == STATUS_CANDIDATE],
        "verify": [r for r in verify if r.targeted],
        "other": [r for r in verify if not r.targeted],
        "excluded": [r for r in results if r.status == STATUS_EXCLUDED],
    }


def _doctor_results(case: str) -> AppTest:
    at = _doctor_with_case(case)
    _click(at, "wiz_search")
    assert at.session_state["wiz_step"] == "results"
    return at


@requires_catalog
@pytest.mark.parametrize("case", list(CASES))
def test_doctor_results_single_ordered_list(case):
    at = _doctor_results(case)
    groups = _doctor_groups(at)
    expected = [(g, r) for g in ("cand", "verify", "other") for r in groups[g]]
    assert not at.tabs
    markdown = [m.value for m in at.markdown]
    if expected:
        assert any(all(label in m for label in SUMMARY_LABELS) for m in markdown)
    else:
        assert any("Žádná" in i.value for i in at.info)

    cards = _cards(at)
    assert len(cards) == len(expected), [k for k, _ in cards]
    parsed = [CARD_KEY.match(k).groups() for k, _ in cards]
    assert [(g, sid) for g, sid, _ in parsed] == [(g, _sanitize(r.study.study_id)) for g, r in expected]
    indices = [int(i) for _, _, i in parsed]
    if indices:
        assert indices[0] in (0, 1) and indices == list(range(indices[0], indices[0] + len(indices)))

    for (group, r), (_, card) in zip(expected, cards):
        heading = _heading(card)
        assert heading.startswith(f"**{GROUP_ICONS[group]} {r.study.acronym}**"), heading
        assert r.study.study_id in heading and r.cohort["label"] in heading
        assert f"doc_ref_{r.study.study_id}" in {getattr(n, "key", None) for n in _walk(card)}

    for r in groups["excluded"]:
        assert not any(f"{r.study.acronym}** · {r.study.study_id} ·" in m for m in markdown), r.study.study_id
    assert not any(m.startswith("**🔴") for m in markdown)
    if groups["excluded"]:
        prefix = f"Vyloučené studie ({len(groups['excluded'])}) nejsou zobrazeny."
        assert any(c.value.startswith(prefix) for c in at.caption)


def _assert_referral_inline(at: AppTest, study_id: str) -> None:
    order = list(_walk(at.main))
    card_pos = [i for i, n in enumerate(order) if (k := _block_key(n)) and CARD_KEY.match(k)]
    own = next(i for i in card_pos if f"doc_ref_{study_id}" in
               {getattr(n, "key", None) for n in _walk(order[i])})
    body = next(i for i, n in enumerate(order) if getattr(n, "key", None) == f"doc_ref_body_{study_id}")
    following = [i for i in card_pos if i > own]
    assert own < body < (following[0] if following else len(order))


@requires_catalog
def test_doctor_referral_inline_send_and_close():
    at = _doctor_results(CASE1)
    groups = _doctor_groups(at)
    picks = [groups[g][0] for g in ("cand", "verify", "other") if groups[g]]
    assert picks
    for r in picks:
        sid = r.study.study_id
        _click(at, f"doc_ref_{sid}")
        assert any("✉️ Referral" in m.value and r.study.acronym in m.value for m in at.markdown)
        _assert_referral_inline(at, sid)
        assert not at.tabs
        _click(at, "doc_ref_close")
        assert f"doc_ref_body_{sid}" not in {t.key for t in at.text_area}
        assert "doc_ref_close" not in {b.key for b in at.button}
        assert len(_cards(at)) == sum(len(groups[g]) for g in ("cand", "verify", "other"))

    sendable = next((r for r in picks + groups["cand"] + groups["verify"] + groups["other"]
                     if not r.study.referral_blockers), None)
    if sendable is None:
        pytest.skip("žádná zobrazená studie nemá kompletní kontakty pro odeslání")
    sid = sendable.study.study_id
    _click(at, f"doc_ref_{sid}")
    assert at.text_area(key=f"doc_ref_body_{sid}").value.strip()
    assert not at.button(key="doc_ref_send").disabled
    _click(at, "doc_ref_send")
    assert any("simul" in s.value.lower() for s in at.success)


@requires_catalog
def test_doctor_referral_send_blocked_without_contacts():
    at = _doctor_results(CASE1)
    groups = _doctor_groups(at)
    blocked = next((r for g in ("cand", "verify", "other") for r in groups[g] if r.study.referral_blockers), None)
    if blocked is None:
        pytest.skip("všechny zobrazené studie mají kompletní kontakty")
    _click(at, f"doc_ref_{blocked.study.study_id}")
    assert at.button(key="doc_ref_send").disabled
    assert any(e.value.startswith("Odeslání blokováno") for e in at.error)


@requires_catalog
def test_patient_results_layout_unchanged():
    at = _run()
    _click(at, "wiz_role_pat")
    at.selectbox(key="pat_demo").select(CASE1).run()
    _click(at, "wiz_demo")
    _click(at, "wiz_search")
    assert at.session_state["wiz_step"] == "results"
    results = [r for r in match(_app_studies(), at.session_state["pat_profile"])
               if r.status in (STATUS_CANDIDATE, STATUS_VERIFY)]
    targeted = [r for r in results if r.targeted]
    others = [r for r in results if not r.targeted]
    assert not at.tabs
    assert not _cards(at)
    assert not [b.key for b in at.button if (b.key or "").startswith("doc_ref")]
    assert any(e.label == f"Studie pro více druhů nádorů ({len(others)})" for e in at.expander)
    headings = [m.value for m in at.markdown if m.value.startswith(("**🟢", "**🟡"))]
    assert len(headings) == len(targeted)
    assert not any("🟠" in m.value for m in at.markdown)

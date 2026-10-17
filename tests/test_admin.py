import json
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest
from streamlit.testing.v1 import AppTest

from trialink import sonis
from trialink.auth import Throttle, hash_password, verify_password
from trialink.catalog import study_from_record, validate
from trialink.demo_cases import case3
from trialink.matching import STATUS_CANDIDATE, evaluate_cohort
from trialink.structuring import draft_criteria, validate_draft

FIELDS = {
    "id_projektu": "2026/99", "cislo_protokolu": "TEST-1", "zkratka": "NOVA-LUNG", "prirazene_diagnozy": "C34",
    "indikace": "plíce, NSCLC KRAS G12C 1. linie", "hlavni_zkousejici": "Test PI", "hlavni_zkousejici_mail": "pi@mou.cz",
    "kontaktni_osoba": "Test SC", "kontaktni_osoba_mail": "sc@mou.cz",
    "kriteria_vstupni_text": "Metastatický NSCLC s mutací KRAS p.G12C.\nECOG 0 nebo 1.",
    "kriteria_vylucujici_text": "Předchozí systémová léčba metastatického onemocnění.",
}

LLM_OUTPUT = {"cohorts": [{"id": "main", "label": "NSCLC G12C", "criteria": [
    {"id": "dg", "kind": "inclusion", "field": "diagnosis", "op": "prefix_in", "value": ["C34"], "quote": "plíce, NSCLC KRAS G12C 1. linie"},
    {"id": "kras", "kind": "inclusion", "field": "biomarker", "op": "positive", "value": {"marker": "KRAS", "variant": "G12C"}, "quote": "s mutací KRAS p.G12C"},
    {"id": "ecog", "kind": "inclusion", "field": "ecog", "op": "<=", "value": 1, "quote": "ECOG 0 nebo 1."},
    {"id": "lines", "kind": "inclusion", "field": "prior_palliative_lines", "op": "between", "value": [0, 0], "quote": "1. linie"},
    {"id": "fake", "kind": "inclusion", "field": "age", "op": ">=", "value": 18, "quote": "věk nad 18 let"},
    {"id": "bad", "kind": "exclusion", "field": "prior_therapy", "op": "received", "value": ["neexistujici skupina"], "quote": "1. linie"},
]}]}


class FakeClient:
    def __init__(self, payload):
        self.payload = payload
        self.chat = SimpleNamespace(completions=SimpleNamespace(create=self._create))

    def _create(self, **kwargs):
        assert "NOVA-LUNG" in kwargs["messages"][1]["content"]
        msg = SimpleNamespace(content=json.dumps(self.payload, ensure_ascii=False))
        return SimpleNamespace(choices=[SimpleNamespace(message=msg)])


def test_record_from_fields_validates():
    rec = sonis.record_from_fields(FIELDS)
    assert rec.study_id == "2026/99" and rec.fields["kontaktni_osoba_klapka"] == "NA"
    with pytest.raises(ValueError):
        sonis.record_from_fields({**FIELDS, "id_projektu": "abc"})
    with pytest.raises(ValueError):
        sonis.record_from_fields({**FIELDS, "kriteria_vstupni_text": ""})


def test_llm_draft_keeps_only_grounded_valid_criteria():
    rec = sonis.record_from_fields(FIELDS)
    draft = draft_criteria(rec, client=FakeClient(LLM_OUTPUT), model="test")
    ids = {c["id"] for c in draft.cohorts[0]["criteria"]}
    assert ids == {"dg", "kras", "ecog", "lines"}
    assert any("citace" in r for r in draft.rejected) and any("neznámé skupiny" in r for r in draft.rejected)

    study = study_from_record(rec, draft.cohorts, "navrh")
    assert study.review == "navrh" and not validate([study]) and not study.referral_blockers
    assert evaluate_cohort(study, study.cohorts[0], case3()).status == STATUS_CANDIDATE


def test_manual_study_without_rules_falls_back_to_review():
    study = study_from_record(sonis.record_from_fields(FIELDS))
    assert study.review == "nestrukturovano"
    assert {c["field"] for c in study.cohorts[0]["criteria"]} == {"diagnosis", "manual"}
    assert validate_draft(study.record, []).cohorts == []


def _admin_app(monkeypatch, stored_hash: str) -> AppTest:
    monkeypatch.setenv("ADMIN_PASSWORD_HASH", stored_hash)
    app_dir = Path(__file__).resolve().parent.parent / "app"
    # In a real run Streamlit puts the main script dir (app/) on sys.path; AppTest only adds app/pages.
    sys.path.insert(0, str(app_dir))
    at = AppTest.from_file(str(app_dir / "pages" / "3_Sprava_studii.py"), default_timeout=60)
    at.run()
    assert not at.exception, at.exception
    return at


def test_admin_page_disabled_without_hash(monkeypatch):
    at = _admin_app(monkeypatch, "")
    assert any("vypnutá" in e.value for e in at.error)
    assert not at.tabs


def test_admin_page_requires_password(monkeypatch):
    at = _admin_app(monkeypatch, hash_password("spravne-heslo-123", iterations=1000))
    assert not at.tabs
    assert {"adm_nav_back", "adm_nav_logo"} <= {b.key for b in at.button}

    def login():
        return next(b for b in at.button if b.label == "Přihlásit")

    at.text_input[0].input("spatne")
    login().click().run()
    assert any("Nesprávné" in e.value for e in at.error) and not at.tabs
    at.text_input[0].input("spravne-heslo-123")
    login().click().run()
    at.run()
    assert not at.exception, at.exception
    assert not any("Nesprávné" in e.value for e in at.error)
    assert len(at.tabs) == 3


def test_password_hash_and_throttle():
    h = hash_password("tajne-heslo-xyz", iterations=1000)
    assert verify_password("tajne-heslo-xyz", h)
    assert not verify_password("tajne-heslo-xyZ", h)
    assert not verify_password("x", "nesmysl") and "tajne" not in h
    t = Throttle(max_failures=3, lockout_s=60)
    for _ in range(3):
        assert t.locked_for(now=100) == 0
        t.fail(now=100)
    assert t.locked_for(now=110) > 0
    assert t.locked_for(now=161) == 0

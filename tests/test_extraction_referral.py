from datetime import date

import pytest
from conftest import requires_export

from trialink.catalog import load_catalog
from trialink.demo_cases import case2, case5
from trialink.extraction import ExtractionError, extract_profile, to_profile
from trialink.matching import match
from trialink.referral import build_referral, simulate_send

REPORT = "Karcinom jícnu, střední část. Histologie: skvamózní karcinom G3. KRAS mutace p.G12C. ECOG PS1. Nepolkne ani sliny."


def _fact(value, evidence):
    return {"value": value, "evidence": evidence}


def _llm_output(**over):
    data = {k: _fact(None, None) for k in ["icd10", "histology", "disease_extent", "setting", "prior_palliative_lines",
                                            "her2_status", "ecog", "birth_year", "sex", "can_swallow"]}
    data.update(therapy_history_known=False, therapies=[], biomarkers=[], conflicts=[])
    data.update(over)
    return data


def test_grounded_facts_kept_ungrounded_dropped():
    data = _llm_output(
        histology=_fact("skvamozni", "skvamózní karcinom G3"),
        ecog=_fact(1, "ECOG PS1"),
        can_swallow=_fact(False, "Nepolkne ani sliny"),
        sex=_fact("M", "muž"),
        biomarkers=[
            {"marker": "KRAS", "status": "pozitivni", "variant": "G12C", "values": [], "method": None, "conflict": False, "evidence": "KRAS mutace p.G12C"},
            {"marker": "BRAF", "status": "negativni", "variant": None, "values": [], "method": None, "conflict": False, "evidence": "BRAF wt"},
        ],
    )
    ex = to_profile(data, REPORT, date(2026, 10, 16))
    assert ex.profile.histology == "skvamozni"
    assert ex.profile.can_swallow is False
    assert ex.profile.sex is None
    assert ex.profile.biomarker("KRAS").variant == "G12C"
    assert ex.profile.biomarker("BRAF") is None
    assert "sex" in ex.dropped and "biomarker BRAF" in ex.dropped
    assert ex.profile.therapies is None


class _FailingClient:
    class chat:
        class completions:
            @staticmethod
            def create(**kwargs):
                assert kwargs["store"] is False
                raise RuntimeError("provider echoed: " + kwargs["messages"][1]["content"])


def test_error_message_never_contains_report():
    with pytest.raises(ExtractionError) as exc:
        extract_profile(REPORT, client=_FailingClient())
    assert "jícnu" not in str(exc.value) and exc.value.__cause__ is None


def test_input_limits():
    with pytest.raises(ExtractionError):
        extract_profile("   ", client=_FailingClient())
    with pytest.raises(ExtractionError):
        extract_profile("x" * 50_000, client=_FailingClient())


@requires_export
def test_referral_simulation_and_blockers():
    studies, _, _ = load_catalog()
    r2 = next(r for r in match(studies, case2()) if r.study.study_id == "2025/R46")
    ref = build_referral(r2, case2())
    roles = {c.role for c in ref.recipients}
    assert roles == {"PI", "SC"} and not ref.blockers
    assert "SIMULACE" in simulate_send(ref)

    r5 = next(r for r in match(studies, case5()) if r.study.study_id == "2024/59")
    blocked = build_referral(r5, case5())
    assert blocked.blockers
    with pytest.raises(ValueError):
        simulate_send(blocked)

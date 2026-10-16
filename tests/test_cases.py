import pytest
from conftest import requires_export

from trialink.catalog import load_catalog
from trialink.demo_cases import case1, case2, case3, case4, case5
from trialink.matching import STATUS_CANDIDATE, STATUS_EXCLUDED, STATUS_VERIFY, evaluate_cohort, match

pytestmark = requires_export


@pytest.fixture(scope="module")
def studies():
    s, _, _ = load_catalog()
    return s


def statuses(studies, profile):
    return {r.study.study_id: r for r in match(studies, profile)}


def failed_ids(result):
    return {r.criterion["id"] for r in result.failed}


def test_case1_crc_g13d(studies):
    r = statuses(studies, case1())
    assert r["2022/41"].status == STATUS_EXCLUDED
    assert "kras" in failed_ids(r["2022/41"])
    assert r["2025/31"].status == STATUS_CANDIDATE
    assert r["2025/45"].status == STATUS_CANDIDATE
    assert r["2024/39R"].status == STATUS_VERIFY


def test_case2_esophagus_second_line(studies):
    r = statuses(studies, case2())
    assert r["2025/R46"].status == STATUS_CANDIDATE
    assert r["2024/R41"].status == STATUS_EXCLUDED
    assert "swallow" in failed_ids(r["2021/18F"])


def test_case3_lung_g12c_high_pdl1(studies):
    r = statuses(studies, case3())
    assert r["2025/50"].status == STATUS_EXCLUDED
    assert failed_ids(r["2025/50"]) == {"pdl1"}
    assert r["2022/41"].status == STATUS_EXCLUDED
    assert r["2023/44"].status == STATUS_EXCLUDED
    assert r["2023/29"].status == STATUS_EXCLUDED
    assert r["2021/33"].status == STATUS_EXCLUDED


def test_case4_her2_breast_after_adcs(studies):
    r = statuses(studies, case4())
    assert r["2025_55"].status == STATUS_CANDIDATE
    eci = next(s for s in studies if s.study_id == "2024/50R")
    breast = next(c for c in eci.cohorts if c["id"] == "breast")
    assert evaluate_cohort(eci, breast, case4()).status == STATUS_EXCLUDED
    assert r["2024/50R"].cohort["id"] == "ccne1"
    assert not r["2024/50R"].targeted


def test_case5_tnbc_neoadjuvant(studies):
    r = statuses(studies, case5())
    assert r["2024/59"].status == STATUS_VERIFY
    open_ids = {x.criterion["id"] for x in r["2024/59"].open_items}
    assert "er" in open_ids
    assert not r["2024/59"].failed
    assert r["2024/48"].status == STATUS_EXCLUDED
    assert r["2024/5"].status == STATUS_EXCLUDED
    assert r["2023/36"].status == STATUS_EXCLUDED


@pytest.mark.parametrize("make", [case1, case2, case3, case4, case5])
def test_top_result_is_targeted(studies, make):
    top = match(studies, make())[0]
    assert top.targeted
    assert top.status != STATUS_EXCLUDED

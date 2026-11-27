import pytest
from mr_quality_monitor.analysis.validation_rules import matches
from mr_quality_monitor.protocols.models import Predicate


@pytest.mark.parametrize(
    "value,target,tolerance,kind,warning,expected",
    [
        (3.3, 3, 0.3, "absolute", None, "pass"),
        (2.7, 3, 0.3, "absolute", None, "pass"),
        (3.31, 3, 0.3, "absolute", None, "fail"),
        (3.5, 3, 0.3, "absolute", 0.5, "warning"),
        (110, 100, 10, "relative_percent", None, "pass"),
        (90, 100, 10, "relative_percent", None, "pass"),
        (110.01, 100, 10, "relative_percent", None, "fail"),
        (-110, -100, 10, "relative_percent", None, "pass"),
        (0, 0, 10, "relative_percent", None, "pass"),
        (0.1, 0, 10, "relative_percent", None, "fail"),
    ],
)
def test_target_boundary_semantics(value, target, tolerance, kind, warning, expected):
    predicate = Predicate.model_validate(
        {
            "field": "SliceThickness",
            "unit": "mm",
            "constraint": {
                "kind": "target",
                "target": target,
                "tolerance": tolerance,
                "tolerance_kind": kind,
                "warning_tolerance": warning,
            },
        }
    )
    assert matches(value, predicate) == expected


@pytest.mark.parametrize(
    "constraint,value,expected",
    [
        ({"kind": "exact", "value": 4000}, 4000, "pass"),
        ({"kind": "exact", "value": 4000}, 4000.01, "fail"),
        ({"kind": "one_of", "values": [3000, 4000]}, 4000, "pass"),
        ({"kind": "one_of", "values": [3000, 4000]}, 3500, "fail"),
    ],
)
def test_exact_and_set_do_not_invent_tolerance(constraint, value, expected):
    predicate = Predicate(field="RepetitionTime", unit="ms", constraint=constraint)
    assert matches(value, predicate) == expected


def test_vector_component_aggregation_is_explicit():
    all_values = Predicate(
        field="PixelSpacing",
        unit="mm",
        components="all",
        constraint={"kind": "range", "minimum": 0.1, "maximum": 1},
    )
    any_value = Predicate(
        field="PixelSpacing",
        unit="mm",
        components="any",
        constraint={"kind": "range", "minimum": 0.1, "maximum": 1},
    )
    assert matches((0.5, 2), all_values) == "fail"
    assert matches((0.5, 2), any_value) == "pass"


def test_text_membership_and_case_policy():
    predicate = Predicate(
        field="ImageType", components="any", constraint={"kind": "one_of", "values": ["ORIGINAL"]}
    )
    assert matches(("ORIGINAL", "PRIMARY"), predicate) == "pass"
    contains = Predicate(
        field="SeriesDescription",
        constraint={
            "kind": "contains",
            "value": "t2",
            "case_sensitive": False,
        },
    )
    assert matches("T2_synthetic", contains) == "pass"

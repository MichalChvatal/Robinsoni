"""Deterministic matching shared by identification and parameter checks."""

from decimal import Decimal
from typing import Literal

from ..protocols.models import Contains, Exact, Excludes, Limit, OneOf, Predicate, Range, Target

Match = Literal["pass", "warning", "fail"]


def scalar_match(value, constraint) -> Match:
    if isinstance(constraint, Exact):
        return "pass" if value == constraint.value else "fail"
    if isinstance(constraint, OneOf):
        return "pass" if value in constraint.values else "fail"
    if isinstance(constraint, Contains):
        if not isinstance(value, str):
            return "fail"
        expected = constraint.value
        if not constraint.case_sensitive:
            value, expected = value.casefold(), expected.casefold()
        found = expected in value
        return "pass" if found != isinstance(constraint, Excludes) else "fail"
    if not isinstance(value, (int, float)) or isinstance(value, bool):
        return "fail"
    if isinstance(constraint, Limit):
        within = (
            value >= constraint.value
            if constraint.bound == "minimum"
            else value <= constraint.value
        )
        return "pass" if within else "fail"
    if isinstance(constraint, Range):
        if constraint.minimum <= value <= constraint.maximum:
            return "pass"
        if constraint.warning_minimum is not None and (
            constraint.warning_minimum <= value <= constraint.warning_maximum
        ):
            return "warning"
        return "fail"
    if isinstance(constraint, Target):
        # Decimal from the normalized decimal spelling preserves inclusive boundaries.
        target, actual = Decimal(str(constraint.target)), Decimal(str(value))
        scale = abs(target) / 100 if constraint.tolerance_kind == "relative_percent" else Decimal(1)
        distance = abs(actual - target)
        if distance <= Decimal(str(constraint.tolerance)) * scale:
            return "pass"
        if constraint.warning_tolerance is not None and (
            distance <= Decimal(str(constraint.warning_tolerance)) * scale
        ):
            return "warning"
        return "fail"
    raise TypeError("Unsupported constraint")


def matches(value, predicate: Predicate) -> Match:
    if predicate.components == "scalar":
        return scalar_match(value, predicate.constraint)
    if predicate.field == "FieldOfView":
        # FOV is a pair (A, B): enforce an independent comparison for each
        # axis and require both axes to satisfy the configured bounds.
        if not isinstance(value, tuple) or len(value) != 2:
            return "fail"
        results = [scalar_match(component, predicate.constraint) for component in value]
        return "fail" if "fail" in results else "pass"
    if not isinstance(value, tuple) or not value:
        return "fail"
    results = [scalar_match(component, predicate.constraint) for component in value]
    if predicate.components == "any":
        return "pass" if "pass" in results else "warning" if "warning" in results else "fail"
    return "fail" if "fail" in results else "warning" if "warning" in results else "pass"

"""Conservative multi-signal identification; uncertainty never wins by rule order."""

from dataclasses import dataclass
from typing import Literal

from ..protocols.models import Protocol
from .metadata_normalizer import Value
from .series_aggregator import AcquisitionGroup
from .validation_rules import matches


def match_study_protocol(records, candidates):
    """Return a protocol only when the header evidence selects a single candidate."""
    selected = set()
    for record in records:
        matching = [
            snapshot
            for snapshot, predicates in candidates
            if any(
                (value := record.known(predicate.field)) is not None
                and matches(value, predicate) == "pass"
                for predicate in predicates
            )
        ]
        if len(matching) == 1:
            selected.add(matching[0])
            if len(selected) > 1:
                return None
    return next(iter(selected), None)


@dataclass(frozen=True)
class IdentificationEvidence:
    field: str
    sop_uid: str | None
    frame_index: int | None
    actual: Value
    state: str
    decision: Literal["match", "mismatch", "unknown"]
    expected: dict
    paths: tuple[str, ...]


@dataclass(frozen=True)
class Candidate:
    sequence_id: str
    decision: Literal["match", "mismatch", "unknown"]
    evidence: tuple[IdentificationEvidence, ...]


@dataclass(frozen=True)
class Identification:
    state: Literal["identified", "unknown", "ambiguous"]
    sequence_id: str | None
    candidates: tuple[Candidate, ...]  # Includes exclusions and their evidence.


def identify(group: AcquisitionGroup, protocol: Protocol) -> Identification:
    candidates = []
    for sequence in protocol.sequences:
        evidence = []
        decisions = set()
        for record in group.members:
            alternatives = set()
            predicates = sequence.identifiers + sequence.any_identifiers
            for index, predicate in enumerate(predicates):
                actual = record.fields.get(predicate.field)
                decision = "unknown"
                if actual is not None and actual.state == "known" and actual.unit == predicate.unit:
                    decision = "mismatch" if matches(actual.value, predicate) == "fail" else "match"
                if index < len(sequence.identifiers):
                    decisions.add(decision)
                else:
                    alternatives.add(decision)
                evidence.append(
                    IdentificationEvidence(
                        predicate.field,
                        record.known("SOPInstanceUID"),
                        record.frame_index,
                        actual.value if actual else None,
                        actual.state if actual else "missing",
                        decision,
                        predicate.model_dump(mode="json"),
                        tuple(source.path for source in actual.sources) if actual else (),
                    )
                )
            if alternatives:
                decisions.add(
                    "match" if "match" in alternatives
                    else "unknown" if "unknown" in alternatives
                    else "mismatch"
                )
        decision = (
            "mismatch"
            if "mismatch" in decisions
            else ("unknown" if not evidence or "unknown" in decisions else "match")
        )
        candidates.append(Candidate(sequence.sequence_id, decision, tuple(evidence)))
    possible = [candidate for candidate in candidates if candidate.decision != "mismatch"]
    if len(possible) == 1 and possible[0].decision == "match":
        return Identification("identified", possible[0].sequence_id, tuple(candidates))
    return Identification("ambiguous" if len(possible) > 1 else "unknown", None, tuple(candidates))

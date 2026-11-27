"""Conservative multi-signal identification; uncertainty never wins by rule order."""

from dataclasses import dataclass
from typing import Literal

from ..protocols.models import Protocol
from .metadata_normalizer import Value
from .series_aggregator import AcquisitionGroup
from .validation_rules import matches


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
        for record in group.members:
            for predicate in sequence.identifiers:
                actual = record.fields.get(predicate.field)
                decision = "unknown"
                if actual is not None and actual.state == "known" and actual.unit == predicate.unit:
                    decision = "mismatch" if matches(actual.value, predicate) == "fail" else "match"
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
        decisions = {item.decision for item in evidence}
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

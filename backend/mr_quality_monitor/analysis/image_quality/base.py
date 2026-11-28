"""Extensible artifact detection interfaces for MR image quality assessment."""

from abc import ABC, abstractmethod
from array import array
from dataclasses import dataclass
from typing import Literal

Status = Literal["GREEN", "ORANGE", "RED", "GRAY"]


@dataclass(frozen=True)
class SlicePixels:
    """Decoded single-frame image slice with minimal geometry for artifact analysis."""

    sop_uid: str
    rows: int
    columns: int
    pixels: array
    phase_direction: str | None = None
    slice_position_mm: float | None = None


@dataclass(frozen=True)
class ArtifactFinding:
    """Structured result of one artifact detector across an acquisition."""

    detector_id: str
    artifact_type: str
    status: Status
    reason: str
    score: float | None
    threshold: dict
    affected_slices: tuple[int, ...] = ()
    action: str = ""


class ArtifactDetector(ABC):
    """Base contract for pluggable image artifact detectors."""

    detector_id: str
    artifact_type: str

    @abstractmethod
    def detect(self, slices: tuple[SlicePixels, ...], *, role: str | None = None) -> ArtifactFinding:
        """Analyze an ordered stack of slices and return a structured finding."""


class DetectorRegistry:
    """Registry allowing new artifact detectors to be added without changing the engine."""

    def __init__(self, detectors: tuple[ArtifactDetector, ...] = ()):
        self._detectors: dict[str, ArtifactDetector] = {}
        for detector in detectors:
            self.register(detector)

    def register(self, detector: ArtifactDetector) -> None:
        if detector.detector_id in self._detectors:
            raise ValueError(f"Duplicate detector ID: {detector.detector_id}")
        self._detectors[detector.detector_id] = detector

    @property
    def detectors(self) -> tuple[ArtifactDetector, ...]:
        return tuple(self._detectors.values())

    def evaluate(
        self,
        slices: tuple[SlicePixels, ...],
        *,
        enabled_detectors: tuple[str, ...] = (),
        role: str | None = None,
    ) -> tuple[ArtifactFinding, ...]:
        selected = (
            [self._detectors[name] for name in enabled_detectors if name in self._detectors]
            if enabled_detectors
            else list(self._detectors.values())
        )
        return tuple(detector.detect(slices, role=role) for detector in selected)

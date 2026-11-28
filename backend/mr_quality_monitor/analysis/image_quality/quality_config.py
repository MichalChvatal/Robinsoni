"""Validated loading of configurable image-quality detector thresholds."""

from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, model_validator


class FrozenConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class SNRBand(FrozenConfig):
    min_snr_ok: float = Field(ge=0, strict=True)
    min_snr_warn: float = Field(ge=0, strict=True)

    @model_validator(mode="after")
    def warning_is_below_ok(self):
        if self.min_snr_warn >= self.min_snr_ok:
            raise ValueError("min_snr_warn must be lower than min_snr_ok")
        return self


class SNRConfig(FrozenConfig):
    default: SNRBand
    roles: dict[str, SNRBand]


class GhostingConfig(FrozenConfig):
    max_ghost_ok: float = Field(ge=0, strict=True)
    max_ghost_crit: float = Field(ge=0, strict=True)

    @model_validator(mode="after")
    def critical_is_above_ok(self):
        if self.max_ghost_crit <= self.max_ghost_ok:
            raise ValueError("max_ghost_crit must be greater than max_ghost_ok")
        return self


class SlabConfig(FrozenConfig):
    max_jump_ok: float = Field(ge=0, strict=True)
    max_jump_crit: float = Field(ge=0, strict=True)

    @model_validator(mode="after")
    def critical_is_above_ok(self):
        if self.max_jump_crit <= self.max_jump_ok:
            raise ValueError("max_jump_crit must be greater than max_jump_ok")
        return self


class ImageQualityConfig(FrozenConfig):
    schema_version: Literal[1]
    low_snr: SNRConfig
    motion_ghosting: GhostingConfig
    slab_discontinuity: SlabConfig


DEFAULT_IMAGE_QUALITY_PATH = (
    Path(__file__).resolve().parents[4] / "config" / "image_quality.yaml"
)


def load_image_quality_config(path: Path | None = None) -> ImageQualityConfig:
    """Read and validate threshold configuration; no silent clinical defaults."""
    source = path or DEFAULT_IMAGE_QUALITY_PATH
    with source.open("r", encoding="utf-8") as stream:
        data = yaml.safe_load(stream)
    return ImageQualityConfig.model_validate(data)

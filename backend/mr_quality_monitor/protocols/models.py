"""Strict DEMO schema: no built-in clinical requirements or implicit tolerances."""

from typing import Annotated, Literal

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StrictBool,
    StrictFloat,
    StrictInt,
    StrictStr,
    model_validator,
)

from ..dicom.metadata_parser import FIELDS

Scalar = StrictStr | StrictInt | StrictFloat
Identifier = Annotated[str, Field(min_length=1, max_length=64, pattern=r"^[A-Za-z0-9_.-]+$")]


class FrozenModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, allow_inf_nan=False)


class Exact(FrozenModel):
    kind: Literal["exact"]
    value: Scalar


class OneOf(FrozenModel):
    kind: Literal["one_of"]
    values: tuple[Scalar, ...] = Field(min_length=1)


class Contains(FrozenModel):
    kind: Literal["contains"]
    value: str = Field(min_length=1)
    case_sensitive: StrictBool = True


class Excludes(Contains):
    kind: Literal["excludes"]


class Range(FrozenModel):
    kind: Literal["range"]
    minimum: float = Field(strict=True)
    maximum: float = Field(strict=True)
    warning_minimum: float | None = Field(default=None, strict=True)
    warning_maximum: float | None = Field(default=None, strict=True)

    @model_validator(mode="after")
    def valid_bounds(self):
        if self.minimum > self.maximum:
            raise ValueError("minimum exceeds maximum")
        if (self.warning_minimum is None) != (self.warning_maximum is None):
            raise ValueError("Both warning bounds are required")
        if self.warning_minimum is not None and not (
            self.warning_minimum <= self.minimum <= self.maximum <= self.warning_maximum
        ):
            raise ValueError("Warning interval must contain the normal interval")
        return self


class Target(FrozenModel):
    kind: Literal["target"]
    target: float = Field(strict=True)
    tolerance: float = Field(ge=0, strict=True)
    tolerance_kind: Literal["absolute", "relative_percent"]
    warning_tolerance: float | None = Field(default=None, ge=0, strict=True)

    @model_validator(mode="after")
    def valid_tolerance(self):
        if self.warning_tolerance is not None and self.warning_tolerance < self.tolerance:
            raise ValueError("Warning tolerance must include normal tolerance")
        return self


class Limit(FrozenModel):
    kind: Literal["limit"]
    bound: Literal["minimum", "maximum"]
    value: float = Field(strict=True)


Constraint = Annotated[
    Exact | OneOf | Contains | Excludes | Range | Target | Limit, Field(discriminator="kind")
]


class Predicate(FrozenModel):
    field: str
    constraint: Constraint
    unit: str | None = None
    components: Literal["scalar", "all", "any"] = "scalar"

    @model_validator(mode="after")
    def valid_field_and_type(self):
        if self.field not in FIELDS:
            raise ValueError("Unknown metadata field")
        spec = FIELDS[self.field]
        if self.unit != spec.unit:
            raise ValueError("Unit must match the normalized metadata unit")
        if (spec.count == 1) != (self.components == "scalar"):
            raise ValueError("Select all/any for multicomponent fields, scalar otherwise")
        numeric = spec.kind in {"int", "float"}
        if isinstance(self.constraint, (Range, Target, Limit)) and not numeric:
            raise ValueError("Numeric constraints require a numeric field")
        if isinstance(self.constraint, Contains) and numeric:
            raise ValueError("contains requires a text field")
        expected = ()
        if isinstance(self.constraint, Exact):
            expected = (self.constraint.value,)
        elif isinstance(self.constraint, OneOf):
            expected = self.constraint.values
        if any(isinstance(value, str) == numeric for value in expected):
            raise ValueError("Expected values do not match the metadata type")
        if spec.kind == "int" and any(float(value) != int(float(value)) for value in expected):
            raise ValueError("Integer field requires integer expected values")
        return self


class ParameterRule(Predicate):
    rule_id: Identifier
    required: StrictBool = True
    severity: Literal["critical", "warning"] = "critical"
    on_missing: Literal["unknown", "critical", "warning"] = "unknown"
    action: str = "Review the acquisition"


class CompletionRule(FrozenModel):
    mode: Literal["explicit", "expected_count", "geometric_stack"] = "explicit"
    expected_members: int | None = Field(default=None, ge=1, strict=True)
    min_members: int | None = Field(default=None, ge=1, strict=True)

    @model_validator(mode="after")
    def valid_expected_count(self):
        if (self.mode == "expected_count") != (self.expected_members is not None):
            raise ValueError("expected_members is required only for expected_count mode")
        if self.mode == "geometric_stack" and self.min_members is None:
            raise ValueError("min_members is required for geometric_stack mode")
        if self.mode != "geometric_stack" and self.min_members is not None:
            raise ValueError("min_members is allowed only for geometric_stack mode")
        return self


class MinValidGroupRule(FrozenModel):
    rule_id: Identifier
    group: str = Field(min_length=1)
    min_valid_count: int = Field(ge=1, strict=True)
    preferred_sequence_id: Identifier | None = None
    missing_preferred_severity: Literal["warning", "critical"] = "warning"
    severity: Literal["warning", "critical"] = "critical"
    action: str = "Verify required sequences in group"


class FallbackGroupRule(FrozenModel):
    rule_id: Identifier
    primary_sequence_ids: tuple[Identifier, ...] = Field(min_length=1)
    fallback_sequence_ids: tuple[Identifier, ...] = Field(min_length=1)
    fallback_used_severity: Literal["warning", "critical"] = "warning"
    severity: Literal["warning", "critical"] = "critical"
    action: str = "Perform fallback RESOLVE acquisition"


class GeometryAlignmentRule(FrozenModel):
    rule_id: Identifier
    reference_sequence_id: Identifier
    target_sequence_ids: tuple[Identifier, ...] = Field(min_length=1)
    position_tolerance_mm: float = Field(default=1.0, ge=0, strict=True)
    orientation_tolerance: float = Field(default=1e-3, ge=0, strict=True)
    spacing_tolerance_mm: float = Field(default=0.1, ge=0, strict=True)
    severity: Literal["warning", "critical"] = "critical"
    action: str = "Align DWI slices to T2 TRA geometry"


class CoverageCheckRule(FrozenModel):
    rule_id: Identifier
    segmentation_sequence_id: Identifier
    target_sequence_ids: tuple[Identifier, ...] = Field(min_length=1)
    margin_mm: float = Field(default=0.0, ge=0, strict=True)
    severity: Literal["warning", "critical"] = "warning"
    action: str = "Verify prostate anatomical coverage"


class StudyRules(FrozenModel):
    min_valid_groups: tuple[MinValidGroupRule, ...] = ()
    fallback_groups: tuple[FallbackGroupRule, ...] = ()
    geometry_alignments: tuple[GeometryAlignmentRule, ...] = ()
    coverage_checks: tuple[CoverageCheckRule, ...] = ()


class SequenceDefinition(FrozenModel):
    sequence_id: Identifier
    display_name: str = Field(min_length=1)
    order: int = Field(ge=1, strict=True)
    required: StrictBool = True
    missing_severity: Literal["critical", "warning"] = "critical"
    group: str | None = None
    role: str | None = None
    identifiers: tuple[Predicate, ...] = Field(min_length=2)
    any_identifiers: tuple[Predicate, ...] = ()
    parameters: tuple[ParameterRule, ...] = ()
    completion: CompletionRule = CompletionRule()
    image_required: StrictBool = True
    image_checks: tuple[str, ...] = ()
    geometry_severity: Literal["unknown", "critical", "warning"] = "unknown"

    @model_validator(mode="after")
    def valid_identifiers(self):
        names = {"SeriesDescription", "ProtocolName", "SequenceName", "StudyDescription"}
        if len({predicate.field for predicate in self.identifiers}) < 2:
            raise ValueError("Identification requires at least two distinct metadata fields")
        if not any(predicate.field not in names for predicate in self.identifiers):
            raise ValueError("Identification must include a non-name metadata signal")
        if len({parameter.rule_id for parameter in self.parameters}) != len(self.parameters):
            raise ValueError("Duplicate parameter rule IDs")
        return self


class PrivateBValueSource(FrozenModel):
    tag: Literal["0019100C", "00211105"]
    creator: str = Field(min_length=1)
    model: str = Field(min_length=1)
    software_version: str = Field(min_length=1)
    vr: Literal["IS", "DS", "SL", "SS", "US", "UL", "FD", "FL"]


class ResolveIdentity(FrozenModel):
    protocol_name: str = Field(min_length=1)
    sequence_name: str = Field(min_length=1)


class ProstateChecks(FrozenModel):
    private_b_values: tuple[PrivateBValueSource, ...] = Field(min_length=1)
    position_tolerance_mm: float | None = Field(default=None, ge=0)
    orientation_tolerance_degrees: float | None = Field(default=None, ge=0, le=90)
    resolve_identities: tuple[ResolveIdentity, ...] = ()


class Protocol(FrozenModel):
    schema_version: Literal[1, 2, 3]
    protocol_id: Identifier
    version: Identifier
    mode: Literal["DEMO", "CLINICAL"]
    display_name: str = Field(min_length=1)
    author: str | None = Field(default=None, min_length=1)
    source: str = Field(min_length=1)
    approved_by: str | None = None
    sequences: tuple[SequenceDefinition, ...] = Field(min_length=1)
    study_rules: StudyRules | None = None
    prostate_checks: ProstateChecks | None = None
    study_identifiers: tuple[Predicate, ...] = ()

    @model_validator(mode="after")
    def unique_definitions(self):
        if self.schema_version == 1 and self.mode != "DEMO":
            raise ValueError("schema_version 1 supports only DEMO mode")
        if self.schema_version == 1 and (
            self.approved_by is not None or self.study_rules is not None or self.study_identifiers
        ):
            raise ValueError("Clinical fields require schema_version 2")
        if any(
            predicate.field not in {
                "StudyDescription", "ProtocolName", "SeriesDescription", "BodyPartExamined"
            }
            for predicate in self.study_identifiers
        ):
            raise ValueError("Study identifiers must use examination or protocol names")
        if self.schema_version < 3 and self.author is None:
            raise ValueError("Protocol author is required")
        if self.schema_version == 2 and self.mode == "CLINICAL" and not (
            self.approved_by and self.approved_by.strip()
        ):
            raise ValueError("CLINICAL protocol requires approved_by")
        if len({item.sequence_id for item in self.sequences}) != len(self.sequences):
            raise ValueError("Duplicate sequence IDs")
        if len({item.order for item in self.sequences}) != len(self.sequences):
            raise ValueError("Duplicate sequence order")
        if self.study_identifiers and self.study_rules is not None:
            sequence_ids = {item.sequence_id for item in self.sequences}
            for rule in self.study_rules.fallback_groups:
                if not set(rule.primary_sequence_ids + rule.fallback_sequence_ids) <= sequence_ids:
                    raise ValueError("Fallback rule references an unknown sequence")
        if self.prostate_checks and not {
            "T2_SAG",
            "T2_COR",
            "T2_TRA",
            "DWI_LOW",
            "DWI_INTERMEDIATE",
            "DWI_HIGH",
            "DWI_ADC",
        }.issubset({item.sequence_id for item in self.sequences}):
            raise ValueError("Prostate checks require all prostate sequence roles")
        return self

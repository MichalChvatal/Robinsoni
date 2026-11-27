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


Constraint = Annotated[Exact | OneOf | Contains | Range | Target, Field(discriminator="kind")]


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
        if isinstance(self.constraint, (Range, Target)) and not numeric:
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
    mode: Literal["explicit", "expected_count"] = "explicit"
    expected_members: int | None = Field(default=None, ge=1, strict=True)

    @model_validator(mode="after")
    def valid_expected_count(self):
        if (self.mode == "expected_count") != (self.expected_members is not None):
            raise ValueError("expected_members is required only for expected_count mode")
        return self


class SequenceDefinition(FrozenModel):
    sequence_id: Identifier
    display_name: str = Field(min_length=1)
    order: int = Field(ge=1, strict=True)
    required: StrictBool = True
    missing_severity: Literal["critical", "warning"] = "critical"
    identifiers: tuple[Predicate, ...] = Field(min_length=2)
    parameters: tuple[ParameterRule, ...] = ()
    completion: CompletionRule = CompletionRule()
    image_required: StrictBool = True
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


class Protocol(FrozenModel):
    schema_version: Literal[1]
    protocol_id: Identifier
    version: Identifier
    mode: Literal["DEMO"]  # Approved clinical protocols require a later explicit schema extension.
    display_name: str = Field(min_length=1)
    author: str = Field(min_length=1)
    source: str = Field(min_length=1)
    sequences: tuple[SequenceDefinition, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def unique_definitions(self):
        if len({item.sequence_id for item in self.sequences}) != len(self.sequences):
            raise ValueError("Duplicate sequence IDs")
        if len({item.order for item in self.sequences}) != len(self.sequences):
            raise ValueError("Duplicate sequence order")
        return self

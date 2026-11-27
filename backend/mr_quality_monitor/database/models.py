"""Intake schema plus append-only metadata/protocol/validation history (version 2)."""

from datetime import UTC, datetime

from sqlalchemy import ForeignKey, String, Text, UniqueConstraint
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


def utc_now() -> str:
    """Store timezone-explicit UTC timestamps, independent of SQLite date handling."""
    return datetime.now(UTC).isoformat()


class Base(DeclarativeBase):
    pass


class SchemaVersion(Base):
    __tablename__ = "schema_version"
    id: Mapped[int] = mapped_column(primary_key=True)
    version: Mapped[int]


class IntakeRecord(Base):
    __tablename__ = "intake_records"
    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    sop_uid: Mapped[str] = mapped_column(String(64), index=True)
    source_ae: Mapped[str] = mapped_column(String(16))
    source_ip: Mapped[str]
    received_at: Mapped[str] = mapped_column(default=utc_now)
    state: Mapped[str] = mapped_column(default="receiving", index=True)
    checksum: Mapped[str | None]


class DicomInstance(Base):
    __tablename__ = "dicom_instances"
    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    sop_uid: Mapped[str] = mapped_column(String(64), unique=True)
    study_uid: Mapped[str] = mapped_column(String(64), index=True)
    series_uid: Mapped[str] = mapped_column(String(64), index=True)
    sop_class_uid: Mapped[str] = mapped_column(String(64))
    transfer_syntax_uid: Mapped[str] = mapped_column(String(64))
    checksum: Mapped[str]
    size_bytes: Mapped[int]
    received_at: Mapped[str]
    integrity_state: Mapped[str] = mapped_column(default="stored")


class ReceptionEvent(Base):
    __tablename__ = "reception_events"
    id: Mapped[int] = mapped_column(primary_key=True)
    intake_id: Mapped[str] = mapped_column(ForeignKey("intake_records.id"), unique=True)
    instance_id: Mapped[str | None] = mapped_column(ForeignKey("dicom_instances.id"))
    outcome: Mapped[str]
    recorded_at: Mapped[str] = mapped_column(default=utc_now)


class AnalysisJob(Base):
    __tablename__ = "analysis_jobs"
    id: Mapped[int] = mapped_column(primary_key=True)
    instance_id: Mapped[str] = mapped_column(ForeignKey("dicom_instances.id"), unique=True)
    kind: Mapped[str] = mapped_column(default="instance_received")
    state: Mapped[str] = mapped_column(default="pending", index=True)
    created_at: Mapped[str] = mapped_column(default=utc_now)


class SystemEvent(Base):
    __tablename__ = "system_events"
    id: Mapped[int] = mapped_column(primary_key=True)
    occurred_at: Mapped[str] = mapped_column(default=utc_now)
    code: Mapped[str]
    correlation_id: Mapped[str | None]


class Patient(Base):
    __tablename__ = "patients"
    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    token: Mapped[str] = mapped_column(String(32), unique=True)
    created_at: Mapped[str] = mapped_column(default=utc_now)


class ProtocolVersion(Base):
    __tablename__ = "protocol_versions"
    __table_args__ = (UniqueConstraint("protocol_id", "version"),)
    sha256: Mapped[str] = mapped_column(String(64), primary_key=True)
    protocol_id: Mapped[str]
    version: Mapped[str]
    content: Mapped[str] = mapped_column(Text)
    created_at: Mapped[str] = mapped_column(default=utc_now)


class SequenceDefinitionRow(Base):
    __tablename__ = "sequence_definitions"
    __table_args__ = (UniqueConstraint("protocol_sha256", "sequence_id"),)
    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    protocol_sha256: Mapped[str] = mapped_column(ForeignKey("protocol_versions.sha256"))
    sequence_id: Mapped[str]
    position: Mapped[int]
    content: Mapped[str] = mapped_column(Text)


class Study(Base):
    __tablename__ = "studies"
    study_uid: Mapped[str] = mapped_column(String(64), primary_key=True)
    patient_id: Mapped[str] = mapped_column(ForeignKey("patients.id"))
    protocol_sha256: Mapped[str] = mapped_column(ForeignKey("protocol_versions.sha256"))
    state: Mapped[str] = mapped_column(default="active")
    revision: Mapped[int] = mapped_column(default=0)
    created_at: Mapped[str] = mapped_column(default=utc_now)


class Series(Base):
    __tablename__ = "series"
    series_uid: Mapped[str] = mapped_column(String(64), primary_key=True)
    study_uid: Mapped[str] = mapped_column(ForeignKey("studies.study_uid"), index=True)
    number: Mapped[int | None]
    description: Mapped[str | None]


class AnalysisBranch(Base):
    __tablename__ = "analysis_branches"
    __table_args__ = (UniqueConstraint("study_uid", "label"),)
    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    study_uid: Mapped[str] = mapped_column(ForeignKey("studies.study_uid"), index=True)
    protocol_sha256: Mapped[str] = mapped_column(ForeignKey("protocol_versions.sha256"))
    label: Mapped[str]
    actor: Mapped[str]
    reason: Mapped[str]
    created_at: Mapped[str] = mapped_column(default=utc_now)


class MetadataSnapshot(Base):
    __tablename__ = "metadata_snapshots"
    sha256: Mapped[str] = mapped_column(String(64), primary_key=True)
    instance_id: Mapped[str] = mapped_column(ForeignKey("dicom_instances.id"), index=True)
    frame_index: Mapped[int]  # -1 for classic single-frame objects
    parser_version: Mapped[str]
    vendor_parser: Mapped[str]
    content: Mapped[str] = mapped_column(Text)
    created_at: Mapped[str] = mapped_column(default=utc_now)


class LogicalAcquisition(Base):
    __tablename__ = "logical_acquisitions"
    sha256: Mapped[str] = mapped_column(String(64), primary_key=True)
    group_key: Mapped[str] = mapped_column(index=True)
    study_uid: Mapped[str] = mapped_column(ForeignKey("studies.study_uid"), index=True)
    series_uid: Mapped[str] = mapped_column(ForeignKey("series.series_uid"))
    content: Mapped[str] = mapped_column(Text)
    created_at: Mapped[str] = mapped_column(default=utc_now)


class AcquisitionMember(Base):
    __tablename__ = "acquisition_members"
    acquisition_sha256: Mapped[str] = mapped_column(
        ForeignKey("logical_acquisitions.sha256"), primary_key=True
    )
    metadata_sha256: Mapped[str] = mapped_column(
        ForeignKey("metadata_snapshots.sha256"), primary_key=True
    )


class ValidationRun(Base):
    __tablename__ = "validation_runs"
    __table_args__ = (
        UniqueConstraint("study_uid", "request_id"),
        UniqueConstraint("branch_id", "sequence_number"),
    )
    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    study_uid: Mapped[str] = mapped_column(ForeignKey("studies.study_uid"), index=True)
    branch_id: Mapped[str] = mapped_column(ForeignKey("analysis_branches.id"), index=True)
    request_id: Mapped[str]
    input_sha256: Mapped[str]
    protocol_sha256: Mapped[str] = mapped_column(ForeignKey("protocol_versions.sha256"))
    engine_version: Mapped[str]
    study_revision: Mapped[int]
    sequence_number: Mapped[int]
    status: Mapped[str]
    evaluated_at: Mapped[str]
    actor: Mapped[str]
    supersedes_id: Mapped[str | None] = mapped_column(ForeignKey("validation_runs.id"))
    content: Mapped[str] = mapped_column(Text)
    content_sha256: Mapped[str]


class ValidationResult(Base):
    __tablename__ = "validation_results"
    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    run_id: Mapped[str] = mapped_column(ForeignKey("validation_runs.id"), index=True)
    acquisition_sha256: Mapped[str | None] = mapped_column(
        ForeignKey("logical_acquisitions.sha256")
    )
    sequence_definition_id: Mapped[str | None] = mapped_column(
        ForeignKey("sequence_definitions.id")
    )
    scope: Mapped[str]  # acquisition or sequence_presence
    status: Mapped[str]
    content: Mapped[str] = mapped_column(Text)


class ValidationIssue(Base):
    __tablename__ = "validation_issues"
    id: Mapped[int] = mapped_column(primary_key=True)
    result_id: Mapped[str] = mapped_column(ForeignKey("validation_results.id"), index=True)
    rule_id: Mapped[str]
    status: Mapped[str]
    reason: Mapped[str]
    content: Mapped[str] = mapped_column(Text)


class HistoryEvent(Base):
    __tablename__ = "history_events"
    id: Mapped[int] = mapped_column(primary_key=True)
    study_uid: Mapped[str] = mapped_column(ForeignKey("studies.study_uid"), index=True)
    code: Mapped[str]
    actor: Mapped[str]
    reference_id: Mapped[str]
    created_at: Mapped[str] = mapped_column(default=utc_now)

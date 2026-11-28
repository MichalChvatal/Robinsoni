"""Add immutable analysis history without changing the intake tables."""

import sqlalchemy as sa
from alembic import op

revision = "0002_history"
down_revision = "0001_intake"
branch_labels = None
depends_on = None

APPEND_ONLY = (
    "patients",
    "protocol_versions",
    "sequence_definitions",
    "analysis_branches",
    "metadata_snapshots",
    "logical_acquisitions",
    "acquisition_members",
    "validation_runs",
    "validation_results",
    "validation_issues",
    "history_events",
)


def text(name, nullable=False, **kwargs):
    return sa.Column(name, sa.Text if name == "content" else sa.String, nullable=nullable, **kwargs)


def identifier(name="id", length=32):
    return sa.Column(name, sa.String(length), primary_key=True)


def fk(name, target, nullable=False, primary_key=False, index=False):
    return sa.Column(
        name,
        sa.String(32 if target.endswith(".id") else 64),
        sa.ForeignKey(target),
        nullable=nullable,
        primary_key=primary_key,
        index=index,
    )


def upgrade():
    op.create_table("patients", identifier(), text("token", unique=True), text("created_at"))
    op.create_table(
        "protocol_versions",
        identifier("sha256", 64),
        text("protocol_id"),
        text("version"),
        text("content"),
        text("created_at"),
        sa.UniqueConstraint("protocol_id", "version"),
    )
    op.create_table(
        "sequence_definitions",
        identifier(),
        fk("protocol_sha256", "protocol_versions.sha256"),
        text("sequence_id"),
        sa.Column("position", sa.Integer, nullable=False),
        text("content"),
        sa.UniqueConstraint("protocol_sha256", "sequence_id"),
    )
    op.create_table(
        "studies",
        identifier("study_uid", 64),
        fk("patient_id", "patients.id"),
        fk("protocol_sha256", "protocol_versions.sha256"),
        text("state"),
        sa.Column("revision", sa.Integer, nullable=False),
        text("created_at"),
    )
    op.create_table(
        "series",
        identifier("series_uid", 64),
        fk("study_uid", "studies.study_uid", index=True),
        sa.Column("number", sa.Integer),
        text("description", nullable=True),
    )
    op.create_table(
        "analysis_branches",
        identifier(),
        fk("study_uid", "studies.study_uid", index=True),
        fk("protocol_sha256", "protocol_versions.sha256"),
        text("label"),
        text("actor"),
        text("reason"),
        text("created_at"),
        sa.UniqueConstraint("study_uid", "label"),
    )
    op.create_table(
        "metadata_snapshots",
        identifier("sha256", 64),
        fk("instance_id", "dicom_instances.id", index=True),
        sa.Column("frame_index", sa.Integer, nullable=False),
        text("parser_version"),
        text("vendor_parser"),
        text("content"),
        text("created_at"),
    )
    op.create_table(
        "logical_acquisitions",
        identifier("sha256", 64),
        text("group_key", index=True),
        fk("study_uid", "studies.study_uid", index=True),
        fk("series_uid", "series.series_uid"),
        text("content"),
        text("created_at"),
    )
    op.create_table(
        "acquisition_members",
        fk("acquisition_sha256", "logical_acquisitions.sha256", primary_key=True),
        fk("metadata_sha256", "metadata_snapshots.sha256", primary_key=True),
    )
    op.create_table(
        "validation_runs",
        identifier(),
        fk("study_uid", "studies.study_uid", index=True),
        fk("branch_id", "analysis_branches.id", index=True),
        text("request_id"),
        text("input_sha256"),
        fk("protocol_sha256", "protocol_versions.sha256"),
        text("engine_version"),
        sa.Column("study_revision", sa.Integer, nullable=False),
        sa.Column("sequence_number", sa.Integer, nullable=False),
        text("status"),
        text("evaluated_at"),
        text("actor"),
        fk("supersedes_id", "validation_runs.id", nullable=True),
        text("content"),
        text("content_sha256"),
        sa.UniqueConstraint("study_uid", "request_id"),
        sa.UniqueConstraint("branch_id", "sequence_number"),
    )
    op.create_table(
        "validation_results",
        identifier(),
        fk("run_id", "validation_runs.id", index=True),
        fk("acquisition_sha256", "logical_acquisitions.sha256", nullable=True),
        fk("sequence_definition_id", "sequence_definitions.id", nullable=True),
        text("scope"),
        text("status"),
        text("content"),
    )
    op.create_table(
        "validation_issues",
        sa.Column("id", sa.Integer, primary_key=True),
        fk("result_id", "validation_results.id", index=True),
        text("rule_id"),
        text("status"),
        text("reason"),
        text("content"),
    )
    op.create_table(
        "history_events",
        sa.Column("id", sa.Integer, primary_key=True),
        fk("study_uid", "studies.study_uid", index=True),
        text("code"),
        text("actor"),
        text("reference_id"),
        text("created_at"),
    )
    for table in APPEND_ONLY:
        for action in ("UPDATE", "DELETE"):
            op.execute(
                f"CREATE TRIGGER {table}_no_{action.lower()} BEFORE {action} ON {table} "
                "BEGIN SELECT RAISE(ABORT, 'append-only history'); END"
            )
    op.execute(
        "CREATE TRIGGER study_binding_immutable BEFORE UPDATE OF "
        "study_uid,patient_id,protocol_sha256 ON studies "
        "BEGIN SELECT RAISE(ABORT, 'immutable study binding'); END"
    )
    op.execute("UPDATE schema_version SET version=2 WHERE id=1")


def downgrade():
    raise RuntimeError("Destructive downgrade is disabled; restore a verified backup instead")

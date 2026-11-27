"""Frozen schema of the original stage-2 intake database."""

import sqlalchemy as sa
from alembic import op

revision = "0001_intake"
down_revision = None
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "schema_version",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("version", sa.Integer, nullable=False),
    )
    op.create_table(
        "intake_records",
        sa.Column("id", sa.String(32), primary_key=True),
        sa.Column("sop_uid", sa.String(64), nullable=False, index=True),
        sa.Column("source_ae", sa.String(16), nullable=False),
        sa.Column("source_ip", sa.String, nullable=False),
        sa.Column("received_at", sa.String, nullable=False),
        sa.Column("state", sa.String, nullable=False, index=True),
        sa.Column("checksum", sa.String),
    )
    op.create_table(
        "dicom_instances",
        sa.Column("id", sa.String(32), primary_key=True),
        sa.Column("sop_uid", sa.String(64), unique=True, nullable=False),
        sa.Column("study_uid", sa.String(64), nullable=False, index=True),
        sa.Column("series_uid", sa.String(64), nullable=False, index=True),
        sa.Column("sop_class_uid", sa.String(64), nullable=False),
        sa.Column("transfer_syntax_uid", sa.String(64), nullable=False),
        sa.Column("checksum", sa.String, nullable=False),
        sa.Column("size_bytes", sa.Integer, nullable=False),
        sa.Column("received_at", sa.String, nullable=False),
        sa.Column("integrity_state", sa.String, nullable=False),
    )
    op.create_table(
        "reception_events",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column(
            "intake_id",
            sa.String(32),
            sa.ForeignKey("intake_records.id"),
            unique=True,
            nullable=False,
        ),
        sa.Column("instance_id", sa.String(32), sa.ForeignKey("dicom_instances.id")),
        sa.Column("outcome", sa.String, nullable=False),
        sa.Column("recorded_at", sa.String, nullable=False),
    )
    op.create_table(
        "analysis_jobs",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column(
            "instance_id",
            sa.String(32),
            sa.ForeignKey("dicom_instances.id"),
            unique=True,
            nullable=False,
        ),
        sa.Column("kind", sa.String, nullable=False),
        sa.Column("state", sa.String, nullable=False, index=True),
        sa.Column("created_at", sa.String, nullable=False),
    )
    op.create_table(
        "system_events",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("occurred_at", sa.String, nullable=False),
        sa.Column("code", sa.String, nullable=False),
        sa.Column("correlation_id", sa.String),
    )
    op.execute("INSERT INTO schema_version (id,version) VALUES (1,1)")


def downgrade():
    raise RuntimeError("Destructive downgrade is disabled; restore a verified backup instead")

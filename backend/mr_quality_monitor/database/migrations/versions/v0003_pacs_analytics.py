"""Add PACS export queue, study analytics and artifact findings tables (version 3)."""

import sqlalchemy as sa
from alembic import op

revision = "0003_pacs_analytics"
down_revision = "0002_history"
branch_labels = None
depends_on = None


def text(name, nullable=False, **kwargs):
    return sa.Column(name, sa.Text if name == "content" else sa.String, nullable=nullable, **kwargs)


def upgrade():
    op.create_table(
        "pacs_exports",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("study_uid", sa.String(64), sa.ForeignKey("studies.study_uid"), nullable=False,
                  index=True),
        sa.Column("series_uid", sa.String(64), nullable=False),
        sa.Column("group_key", sa.String(64), nullable=False),
        # status: queued | awaiting_approval | approved | sent | failed
        text("status"),
        text("created_at"),
        sa.Column("approved_at", sa.String, nullable=True),
        sa.Column("sent_at", sa.String, nullable=True),
        sa.Column("error_code", sa.String, nullable=True),
    )
    op.create_table(
        "study_analytics",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("study_uid", sa.String(64), sa.ForeignKey("studies.study_uid"), nullable=False,
                  index=True, unique=True),
        sa.Column("operator_name", sa.String, nullable=True),
        sa.Column("patient_age_years", sa.Integer, nullable=True),
        sa.Column("patient_weight_kg", sa.Float, nullable=True),
        sa.Column("patient_bmi", sa.Float, nullable=True),
        sa.Column("patient_size_m", sa.Float, nullable=True),
        text("recorded_at"),
    )
    op.create_table(
        "artifact_findings",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("result_id", sa.String(32), sa.ForeignKey("validation_results.id"),
                  nullable=False, index=True),
        text("detector_id"),
        text("artifact_type"),
        text("status"),
        sa.Column("score", sa.Float, nullable=True),
        # JSON blobs
        sa.Column("threshold_json", sa.Text, nullable=True),
        sa.Column("affected_slices_json", sa.Text, nullable=True),
    )
    op.execute("UPDATE schema_version SET version=3 WHERE id=1")


def downgrade():
    raise RuntimeError("Destructive downgrade is disabled; restore a verified backup instead")

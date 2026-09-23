"""intentos_verificacion (US2, data-model.md).

Revision ID: 0002_intentos_verificacion
Revises: 0001_pasajeros
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql as pg

revision = "0002_intentos_verificacion"
down_revision = "0001_pasajeros"
branch_labels = None
depends_on = None

SCORE = sa.Numeric(4, 3)


def upgrade() -> None:
    op.create_table(
        "intentos_verificacion",
        sa.Column("id", pg.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "pasajero_id",
            pg.UUID(as_uuid=True),
            sa.ForeignKey("pasajeros.id", name="fk_intentos_verificacion_pasajero_id_pasajeros"),
            nullable=False,
        ),
        sa.Column("selfie_blob_pathname", sa.Text),
        sa.Column("selfie_blob_url", sa.Text),
        sa.Column(
            "resultado", pg.ENUM(name="resultado_intento", create_type=False), nullable=False
        ),
        sa.Column("motivo_fallo", pg.ENUM(name="motivo_fallo", create_type=False)),
        sa.Column("score_liveness", SCORE),
        sa.Column("score_comparacion", SCORE),
        sa.Column("umbral_liveness", SCORE, nullable=False),
        sa.Column("umbral_comparacion", SCORE, nullable=False),
        sa.Column("proveedor", sa.Text, nullable=False),
        sa.Column("imagen_eliminada_at", sa.DateTime(timezone=True)),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "(resultado = 'FALLIDO') = (motivo_fallo IS NOT NULL)",
            name="ck_intentos_verificacion_motivo_solo_si_fallido",
        ),
        sa.CheckConstraint(
            "(resultado = 'NO_CONCLUYENTE') = "
            "(score_liveness IS NULL AND score_comparacion IS NULL)",
            name="ck_intentos_verificacion_scores_segun_resultado",
        ),
        sa.CheckConstraint(
            "score_liveness BETWEEN 0 AND 1 AND score_comparacion BETWEEN 0 AND 1",
            name="ck_intentos_verificacion_scores_rango",
        ),
        sa.CheckConstraint(
            "selfie_blob_pathname IS NOT NULL OR imagen_eliminada_at IS NOT NULL",
            name="ck_intentos_verificacion_selfie_o_eliminada",
        ),
    )
    op.create_index(
        "ix_intentos_verificacion_pasajero_id", "intentos_verificacion", ["pasajero_id"]
    )


def downgrade() -> None:
    op.drop_table("intentos_verificacion")

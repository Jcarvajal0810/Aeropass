"""identidades_digitales + outbox_eventos (US3, data-model.md).

Revision ID: 0003_identidades_outbox
Revises: 0002_intentos_verificacion
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql as pg

revision = "0003_identidades_outbox"
down_revision = "0002_intentos_verificacion"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "identidades_digitales",
        sa.Column("id", pg.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "pasajero_id",
            pg.UUID(as_uuid=True),
            sa.ForeignKey("pasajeros.id", name="fk_identidades_digitales_pasajero_id_pasajeros"),
            nullable=False,
        ),
        sa.Column(
            "intento_origen_id",
            pg.UUID(as_uuid=True),
            sa.ForeignKey(
                "intentos_verificacion.id",
                name="fk_identidades_digitales_intento_origen",
            ),
            nullable=False,
        ),
        sa.Column("estado", pg.ENUM(name="estado_identidad", create_type=False), nullable=False),
        sa.Column("revocada_at", sa.DateTime(timezone=True)),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("intento_origen_id", name="uq_identidades_digitales_intento_origen_id"),
        sa.CheckConstraint(
            "(estado = 'REVOCADA') = (revocada_at IS NOT NULL)",
            name="ck_identidades_digitales_revocada_at",
        ),
    )
    # FR-011: at most one ACTIVE identity per passenger.
    op.create_index(
        "uq_identidades_digitales_una_activa",
        "identidades_digitales",
        ["pasajero_id"],
        unique=True,
        postgresql_where=sa.text("estado = 'ACTIVA'"),
    )

    op.create_table(
        "outbox_eventos",
        sa.Column("id", pg.UUID(as_uuid=True), primary_key=True),
        sa.Column("tipo", sa.Text, nullable=False),
        sa.Column("version", sa.SmallInteger, nullable=False),
        sa.Column("payload", pg.JSONB, nullable=False),
        sa.Column(
            "estado",
            pg.ENUM(name="estado_evento", create_type=False),
            nullable=False,
            server_default="PENDIENTE",
        ),
        sa.Column("intentos", sa.Integer, nullable=False, server_default="0"),
        sa.Column(
            "proximo_intento_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column("ultimo_error", sa.Text),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("entregado_at", sa.DateTime(timezone=True)),
    )
    op.create_index(
        "ix_outbox_eventos_pendientes",
        "outbox_eventos",
        ["proximo_intento_at"],
        postgresql_where=sa.text("estado = 'PENDIENTE'"),
    )


def downgrade() -> None:
    op.drop_table("outbox_eventos")
    op.drop_table("identidades_digitales")

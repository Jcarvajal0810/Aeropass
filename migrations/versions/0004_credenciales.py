"""credenciales_acceso + transiciones_credencial (US4, data-model.md).

Revision ID: 0004_credenciales
Revises: 0003_identidades_outbox
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql as pg

revision = "0004_credenciales"
down_revision = "0003_identidades_outbox"
branch_labels = None
depends_on = None


def _estado() -> pg.ENUM:
    return pg.ENUM(name="estado_credencial", create_type=False)


def upgrade() -> None:
    op.create_table(
        "credenciales_acceso",
        sa.Column("id", pg.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "pasajero_id",
            pg.UUID(as_uuid=True),
            sa.ForeignKey("pasajeros.id", name="fk_credenciales_acceso_pasajero"),
            nullable=False,
        ),
        sa.Column(
            "identidad_id",
            pg.UUID(as_uuid=True),
            sa.ForeignKey("identidades_digitales.id", name="fk_credenciales_acceso_identidad"),
            nullable=False,
        ),
        sa.Column("codigo_vuelo", sa.Text, nullable=False),
        sa.Column("permisos", pg.ARRAY(sa.Text), nullable=False),
        sa.Column("firma", sa.Text, nullable=False),
        sa.Column("kid", sa.Text, nullable=False),
        sa.Column("emitida_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expira_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("estado", _estado(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        # FR-016 / SC-003: every credential lives 30–60 s.
        sa.CheckConstraint(
            "expira_at - emitida_at BETWEEN interval '30 seconds' AND interval '60 seconds'",
            name="ck_credenciales_acceso_vigencia",
        ),
        sa.CheckConstraint(
            "cardinality(permisos) > 0", name="ck_credenciales_acceso_permisos_no_vacios"
        ),
        sa.CheckConstraint(
            "codigo_vuelo ~ '^[A-Z0-9]{2}[0-9]{1,4}[A-Z]?$'",
            name="ck_credenciales_acceso_codigo_vuelo",
        ),
    )
    # FR-020: at most one live credential per passenger and flight.
    op.create_index(
        "uq_credenciales_acceso_una_viva",
        "credenciales_acceso",
        ["pasajero_id", "codigo_vuelo"],
        unique=True,
        postgresql_where=sa.text("estado IN ('EMITIDA', 'ACTIVA')"),
    )
    op.create_index(
        "ix_credenciales_acceso_estado_expira", "credenciales_acceso", ["estado", "expira_at"]
    )

    op.create_table(
        "transiciones_credencial",
        sa.Column("id", sa.BigInteger, sa.Identity(), primary_key=True),
        sa.Column(
            "credencial_id",
            pg.UUID(as_uuid=True),
            sa.ForeignKey("credenciales_acceso.id", name="fk_transiciones_credencial_credencial"),
            nullable=False,
        ),
        sa.Column("estado_anterior", _estado()),
        sa.Column("estado_solicitado", _estado(), nullable=False),
        sa.Column("aceptada", sa.Boolean, nullable=False),
        sa.Column("motivo", sa.Text, nullable=False),
        sa.Column("actor", sa.Text, nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index(
        "ix_transiciones_credencial_credencial_id", "transiciones_credencial", ["credencial_id"]
    )

    # FR-018: the history is append-only.
    op.execute(
        """
        CREATE FUNCTION transiciones_credencial_inmutable() RETURNS trigger AS $$
        BEGIN
            RAISE EXCEPTION 'transiciones_credencial is append-only (FR-018)';
        END;
        $$ LANGUAGE plpgsql
        """
    )
    op.execute(
        """
        CREATE TRIGGER transiciones_credencial_inmutable
        BEFORE UPDATE OR DELETE ON transiciones_credencial
        FOR EACH ROW EXECUTE FUNCTION transiciones_credencial_inmutable()
        """
    )


def downgrade() -> None:
    op.execute(
        "DROP TRIGGER IF EXISTS transiciones_credencial_inmutable ON transiciones_credencial"
    )
    op.execute("DROP FUNCTION IF EXISTS transiciones_credencial_inmutable()")
    op.drop_table("transiciones_credencial")
    op.drop_table("credenciales_acceso")

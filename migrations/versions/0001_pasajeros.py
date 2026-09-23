"""pasajeros (US1, data-model.md).

Revision ID: 0001_pasajeros
Revises: 0000_enums
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql as pg

revision = "0001_pasajeros"
down_revision = "0000_enums"
branch_labels = None
depends_on = None


def _enum(name: str) -> pg.ENUM:
    return pg.ENUM(name=name, create_type=False)


def upgrade() -> None:
    op.create_table(
        "pasajeros",
        sa.Column("id", pg.UUID(as_uuid=True), primary_key=True),
        sa.Column("clerk_user_id", sa.Text, nullable=False),
        sa.Column("nombre_completo", sa.Text, nullable=False),
        sa.Column("tipo_documento", _enum("tipo_documento"), nullable=False),
        sa.Column("numero_documento", sa.Text, nullable=False),
        sa.Column("fecha_vencimiento_documento", sa.Date, nullable=False),
        sa.Column("foto_documento_blob_pathname", sa.Text, nullable=False),
        sa.Column("foto_documento_blob_url", sa.Text, nullable=False),
        sa.Column("estado", _enum("estado_pasajero"), nullable=False),
        sa.Column("intentos_fallidos", sa.SmallInteger, nullable=False, server_default="0"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("clerk_user_id", name="uq_pasajeros_clerk_user_id"),
        sa.UniqueConstraint("tipo_documento", "numero_documento", name="uq_pasajeros_documento"),
        sa.CheckConstraint(
            "intentos_fallidos BETWEEN 0 AND 3", name="ck_pasajeros_intentos_fallidos_rango"
        ),
        sa.CheckConstraint(
            "char_length(nombre_completo) BETWEEN 2 AND 200", name="ck_pasajeros_nombre_longitud"
        ),
        sa.CheckConstraint(
            "numero_documento ~ '^[A-Z0-9]{4,20}$'", name="ck_pasajeros_numero_formato"
        ),
    )
    op.create_index("ix_pasajeros_estado", "pasajeros", ["estado"])


def downgrade() -> None:
    op.drop_table("pasajeros")

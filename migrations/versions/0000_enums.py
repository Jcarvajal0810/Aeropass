"""PostgreSQL enum types (data-model.md, "Enumeraciones").

Values are frozen here on purpose: later enum changes need their own migration.

Revision ID: 0000_enums
Revises:
"""

from alembic import op

revision = "0000_enums"
down_revision = None
branch_labels = None
depends_on = None

ENUMS: dict[str, tuple[str, ...]] = {
    "tipo_documento": ("CC", "CE", "PASAPORTE"),
    "estado_pasajero": ("PENDIENTE_VERIFICACION", "VERIFICADO", "REQUIERE_REVISION_MANUAL"),
    "resultado_intento": ("EXITOSO", "FALLIDO", "NO_CONCLUYENTE"),
    "motivo_fallo": ("LIVENESS", "COMPARACION"),
    "estado_identidad": ("ACTIVA", "REVOCADA"),
    "estado_credencial": ("EMITIDA", "ACTIVA", "CONSUMIDA", "EXPIRADA", "REVOCADA"),
    "estado_evento": ("PENDIENTE", "ENTREGADO"),
}


def upgrade() -> None:
    for name, values in ENUMS.items():
        literals = ", ".join(f"'{v}'" for v in values)
        op.execute(f"CREATE TYPE {name} AS ENUM ({literals})")


def downgrade() -> None:
    for name in reversed(list(ENUMS)):
        op.execute(f"DROP TYPE IF EXISTS {name}")

# Quickstart: validar la feature 001 de punta a punta

Guía para comprobar que el flujo registro → selfie → identidad → QR funciona. Los contratos
están en [contracts/openapi.yaml](contracts/openapi.yaml) y el modelo en
[data-model.md](data-model.md); aquí solo se describen los pasos y los resultados esperados.

## Prerrequisitos

- Python 3.11+ y [`uv`](https://docs.astral.sh/uv/).
- Postgres local para pruebas (`docker run -p 5432:5432 -e POSTGRES_PASSWORD=postgres postgres:16`)
  o una rama de Neon.
- Para el entorno real: proyecto en Vercel con Neon, un Blob store **privado**, una base de
  Upstash Redis y QStash conectados, y una aplicación de Clerk.

## Variables de entorno

| Variable | Uso |
|---|---|
| `DATABASE_URL` | Neon pooled (`postgresql+asyncpg://…-pooler…`) |
| `DATABASE_URL_DIRECT` | Neon directa, solo para Alembic |
| `BLOB_READ_WRITE_TOKEN` | Vercel Blob |
| `UPSTASH_REDIS_REST_URL`, `UPSTASH_REDIS_REST_TOKEN` | Redis |
| `QSTASH_TOKEN`, `QSTASH_CURRENT_SIGNING_KEY`, `QSTASH_NEXT_SIGNING_KEY` | QStash |
| `QSTASH_EVENTS_URL_GROUP` | URL group de destino de eventos (`aeropass-eventos`) |
| `CLERK_SECRET_KEY` | Clerk |
| `QR_SIGNING_PRIVATE_KEY`, `QR_SIGNING_KID` | Firma Ed25519 del QR |
| `BIOMETRIC_PROVIDER` | `mock` (local/pruebas) o `vision` |
| `BIOMETRIC_LIVENESS_THRESHOLD`, `BIOMETRIC_MATCH_THRESHOLD` | Por defecto `0.80` |
| `QR_TTL_SECONDS` | 30–60, por defecto `45` |

Para desarrollo local sin servicios externos: `AEROPASS_ADAPTERS=fake` reemplaza Blob, Redis,
QStash y Clerk por dobles en memoria (el token de Clerk se simula con `Authorization: Bearer
test:<user_id>`; la firma de QStash con `Upstash-Signature: test`). **Postgres sigue siendo real**
(`DATABASE_URL` local o rama de Neon) en ambos modos.

## Configuración

```bash
uv sync
uv run alembic upgrade head          # crea tablas, enums, índices parciales y trigger
uv run python -m aeropass.tools.gen_signing_key   # imprime un par Ed25519 para .env
uv run python -m aeropass.tools.make_mock_images ./mock-images   # ok/spoof/other/timeout/documento .jpg
uv run uvicorn aeropass.main:app --reload
```

## Pruebas automáticas

```bash
uv run pytest tests/unit                     # dominio: State, Builder, reglas de intentos
uv run pytest tests/integration              # requiere TEST_DATABASE_URL
uv run pytest tests/contract                 # respuestas vs openapi.yaml, eventos vs JSON Schema
```

Esperado: todo en verde. Pruebas clave:

| Prueba | Demuestra |
|---|---|
| `test_credential_states.py` | Tabla de transiciones completa; `CONSUMIDA → CONSUMIDA` rechazada (RN-06) |
| `test_credential_builder.py` | No se construye sin firma, vuelo, permisos o con TTL fuera de 30–60 s |
| `test_concurrent_consume.py` | 50 consumos simultáneos del mismo `jti` → exactamente 1 aceptado (SC-004) |
| `test_concurrent_passes.py` | 2 emisiones simultáneas → 1 `ACTIVA`, 1 `REVOCADA` |
| `test_outbox_recovery.py` | Con QStash caído la identidad se crea; al recuperarse el dispatcher entrega el evento en < 5 min simulados (SC-006) |
| `test_rejected_transition_persisted.py` | Una transición rechazada queda registrada aunque la operación falle (FR-019) |
| `test_circuit_breaker.py` | 5 fallos abren el circuito; la siguiente selfie responde `NO_CONCLUYENTE` al instante |
| `test_transitions_immutable.py` | `UPDATE`/`DELETE` sobre `transiciones_credencial` falla |

## Recorrido manual (con `AEROPASS_ADAPTERS=fake` y `BIOMETRIC_PROVIDER=mock`)

El mock decide por un marcador incrustado en los bytes de la selfie (`MOCK:ok`, `MOCK:spoof`,
`MOCK:other`, `MOCK:timeout`); las imágenes generadas por `make_mock_images` ya lo llevan:
`ok.jpg` → éxito, `spoof.jpg` → falla prueba de vida, `other.jpg` → falla comparación,
`timeout.jpg` → simula caída del proveedor (espera 4 s).

1. **Registro** — `POST /v1/identity` (multipart) con los datos de un documento vigente y
   `foto_documento=@mock-images/documento.jpg`.
   Esperado: `201`, `estado = PENDIENTE_VERIFICACION`, la foto existe en el store de medios fake y
   `pasajeros` solo guarda su referencia. Repetir: `200` con el mismo `id`.
   Con `fecha_vencimiento` pasada: `422 DOCUMENTO_VENCIDO`. Sin foto: `422 DATOS_INVALIDOS`.
2. **Selfie fallida** — `POST /v1/biometrics/verifications` con `spoof.jpg`.
   Esperado: `FALLIDO`, `motivo_fallo = LIVENESS`, `intentos_restantes = 2`.
3. **Proveedor caído** — enviar `timeout.jpg` 5 veces.
   Esperado: `NO_CONCLUYENTE`; `intentos_restantes` sigue en 2; a partir del 6.º envío la
   respuesta llega en menos de 1 s (circuito abierto).
4. **Selfie exitosa** — esperar 30 s y enviar `ok.jpg`.
   Esperado: `EXITOSO`, `estado_pasajero = VERIFICADO`, `identidad_id` presente. En la base:
   una fila en `outbox_eventos` en `ENTREGADO` (o `PENDIENTE` si el fake de QStash está en modo
   caída). En el store de medios: el blob existe y `intentos_verificacion` solo guarda la URL.
5. **Pase** — `POST /v1/passes` con `{"codigo_vuelo": "AV9380"}`.
   Esperado: `201`, `expira_at - emitida_at` entre 30 y 60 s, `token` verificable con
   `/.well-known/jwks.json`. `GET /v1/passes/{id}` muestra historial `EMITIDA → ACTIVA`.
6. **Renovación** — repetir el paso 5. Esperado: el pase anterior queda `REVOCADA` (motivo
   `RENOVACION`) y su clave `qr:{jti}` ya no existe.
7. **Expiración** — esperar a que venza el pase y consultar `GET /v1/passes/{id}`.
   Esperado: `EXPIRADA`.
8. **Límite** — emitir 31 pases en menos de un minuto. Esperado: el 31.º responde
   `429 LIMITE_EMISION_EXCEDIDO` con `Retry-After`.

## Validación en Vercel

```bash
vercel deploy                        # preview
vercel env pull .env.local
```

- Crear el schedule de QStash que llama a `POST <deploy>/internal/outbox/dispatch` cada minuto.
- Repetir los pasos 1–6 con un token real de Clerk y `BIOMETRIC_PROVIDER=mock`.
- Comprobar que la URL del blob devuelve `401/403` sin el token del store (FR-010).
- Medir la emisión de pases con la instancia caliente:
  `uv run python -m aeropass.tools.bench_passes --base-url <deploy> --tokens tokens.txt --n 200`
  (`tokens.txt`: un token de Clerk por cuenta de prueba ya verificada). Esperado: p95 < 1 s
  (SC-002). El script reparte las emisiones entre las cuentas para no chocar con el límite de
  30/min.

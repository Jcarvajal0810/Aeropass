# Aeropass

Backend de AeroPass: registro del documento, verificación biométrica (prueba de vida + comparación),
identidad digital y QR dinámico de un solo uso. Python 3.11+ · FastAPI · Neon Postgres · Vercel Blob
· Upstash Redis · Upstash QStash · Clerk · Vercel.

- Constitución del proyecto: [`.specify/memory/constitution.md`](.specify/memory/constitution.md)
- Especificación, plan y contratos de la feature 001:
  [`specs/001-persistencia-registro-qr/`](specs/001-persistencia-registro-qr/)
  ([plan](specs/001-persistencia-registro-qr/plan.md),
  [OpenAPI](specs/001-persistencia-registro-qr/contracts/openapi.yaml),
  [puntos de extensión](specs/001-persistencia-registro-qr/contracts/extension-points.md))

## Arquitectura

```text
src/aeropass/
├── domain/     reglas de negocio puras (estados, Builder del QR, firma, reglas biométricas)
├── ports/      abstracciones (Protocol) de todo lo externo
├── services/   casos de uso (Facade de verificación, emisión de pases, outbox…)
├── adapters/   Neon, Blob, Redis, QStash, Clerk, proveedor biométrico + dobles en memoria
└── api/        FastAPI; deps.py es la única raíz de composición
```

| Endpoint | Qué hace |
|---|---|
| `POST /v1/identity` | Registra datos + foto del documento (multipart) |
| `GET /v1/identity/me` | Estado del pasajero |
| `POST /v1/biometrics/verifications` | Selfie → prueba de vida + comparación → identidad digital |
| `POST /v1/passes` | Emite o renueva el QR (30–60 s, firmado Ed25519) |
| `GET /v1/passes/{id}` | Estado e historial del QR |
| `GET /.well-known/jwks.json` | Claves públicas para verificar el QR |
| `POST /internal/outbox/dispatch` | Schedule de QStash: reintenta eventos y expira QRs |

## Desarrollo local

Requisitos: [`uv`](https://docs.astral.sh/uv/). No hace falta Docker.

```bash
uv sync                                   # instala Python 3.12 y dependencias
cp .env.example .env                      # AEROPASS_ADAPTERS=fake por defecto
uv run python -m aeropass.tools.gen_signing_key      # pega la salida en .env
uv run python -m aeropass.tools.make_mock_images ./mock-images
uv run alembic upgrade head               # requiere DATABASE_URL (Postgres local o rama de Neon)
uv run uvicorn aeropass.main:app --reload
```

**Modos de adaptadores** (`AEROPASS_ADAPTERS`):

- `fake`: Clerk, Blob, Redis y QStash se reemplazan por dobles en memoria.
  - Autenticación: `Authorization: Bearer test:<user_id>`.
  - Firma de QStash: `Upstash-Signature: test`.
- `real`: servicios reales.

Postgres es **siempre** real en ambos modos.

El proveedor biométrico `mock` decide por la marca en la imagen. Las imágenes de `make_mock_images` son:

| Imagen | Resultado |
|---|---|
| `ok.jpg` | Verificación exitosa |
| `spoof.jpg` | Falla la prueba de vida |
| `other.jpg` | Falla la comparación con el documento |
| `timeout.jpg` | Simula una caída del proveedor |

## Pruebas

```bash
uv run pytest                 # unitarias, de contrato y de integración
uv run ruff check . && uv run ruff format --check . && uv run mypy
```

Las pruebas de integración usan `TEST_DATABASE_URL` si está definida. Si no, levantan un Postgres
embebido con `pgserver` (dependencia de desarrollo; datos en `.pgdata/`).

## Despliegue en Vercel

1. Conecta Neon, un Blob store **privado**, Upstash Redis y QStash al proyecto. Define las variables
   de [`.env.example`](.env.example) con `AEROPASS_ADAPTERS=real`.
2. Ejecuta las migraciones contra la URL **directa** de Neon:
   `DATABASE_URL_DIRECT=… uv run alembic upgrade head`.
3. Despliega (`vercel deploy`). El entrypoint es [`api/index.py`](api/index.py) y la región
   (`gru1`) está en [`vercel.json`](vercel.json). Ubica Neon, Blob y Redis en la misma región.
4. Crea el schedule del dispatcher (idempotente):
   `uv run python -m aeropass.tools.setup_qstash_schedule`, con `QSTASH_TOKEN` y `PUBLIC_BASE_URL`
   definidos.
5. Mide la latencia de emisión (SC-002, p95 < 1 s):
   `uv run python -m aeropass.tools.bench_passes --base-url https://<deploy> --tokens tokens.txt`.

### Rotación de la clave de firma del QR

1. Genera una clave nueva con otro `kid`:
   `uv run python -m aeropass.tools.gen_signing_key qr-2027`.
2. Actualiza `QR_SIGNING_PRIVATE_KEY` y `QR_SIGNING_KID`.

Cada QR vive a lo sumo 60 s, así que los emitidos con la clave anterior caducan solos. Los
checkpoints obtienen la clave nueva desde `/.well-known/jwks.json`, que se cachea 5 min.

## Fuera de alcance de este repo

Estas partes las hacen otros equipos:

- Validación en el checkpoint (`validate`).
- Consola del agente humano.
- Integración con la aerolínea o el GDS (`flights`).
- Observabilidad completa.

Este repo deja listos los puntos de extensión que esas partes necesitan: `CredentialLifecycleService.consume` (aplica RN-06), la JWKS,
los esquemas de eventos, los decoradores `@traced`/`@audited` y `FlightCatalog`.

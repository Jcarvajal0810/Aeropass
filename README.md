# Aeropass

AeroPass backend: identity document registration, biometric verification (liveness + face match),
digital identity and single-use dynamic QR codes. Python 3.11+ · FastAPI · Neon Postgres · Vercel Blob
· Upstash Redis · Upstash QStash · Clerk · Vercel.

- Project constitution: [`.specify/memory/constitution.md`](.specify/memory/constitution.md)
- Specification, plan and contracts for feature 001:
  [`specs/001-persistencia-registro-qr/`](specs/001-persistencia-registro-qr/)
  ([plan](specs/001-persistencia-registro-qr/plan.md),
  [OpenAPI](specs/001-persistencia-registro-qr/contracts/openapi.yaml),
  [extension points](specs/001-persistencia-registro-qr/contracts/extension-points.md))

## Architecture

```text
src/aeropass/
├── domain/     pure business rules (states, QR Builder, signing, biometric rules)
├── ports/      abstractions (Protocol) for everything external
├── services/   use cases (verification Facade, pass issuance, outbox…)
├── adapters/   Neon, Blob, Redis, QStash, Clerk, biometric provider + in-memory doubles
└── api/        FastAPI; deps.py is the single composition root
```

| Endpoint | What it does |
|---|---|
| `POST /v1/identity` | Registers the document data + photo (multipart) |
| `GET /v1/identity/me` | Passenger status |
| `POST /v1/biometrics/verifications` | Selfie → liveness + face match → digital identity |
| `POST /v1/passes` | Issues or renews the QR (30–60 s, Ed25519-signed) |
| `GET /v1/passes/{id}` | QR status and history |
| `GET /.well-known/jwks.json` | Public keys to verify the QR |
| `POST /internal/outbox/dispatch` | QStash schedule: retries events and expires QRs |

See [Endpoints](#endpoints) for the full frontend integration guide.

## Local development

Requirements: [`uv`](https://docs.astral.sh/uv/). Docker is not needed.

```bash
uv sync                                   # installs Python 3.12 and dependencies
cp .env.example .env                      # AEROPASS_ADAPTERS=fake by default
uv run python -m aeropass.tools.gen_signing_key      # paste the output into .env
uv run python -m aeropass.tools.make_mock_images ./mock-images
uv run alembic upgrade head               # requires DATABASE_URL (local Postgres or a Neon branch)
uv run uvicorn aeropass.main:app --reload
```

**Adapter modes** (`AEROPASS_ADAPTERS`):

- `fake`: Clerk, Blob, Redis and QStash are replaced by in-memory doubles.
  - Authentication: `Authorization: Bearer test:<user_id>`.
  - QStash signature: `Upstash-Signature: test`.
- `real`: real services.

Postgres is **always** real in both modes.

The `mock` biometric provider decides based on a marker in the image. The images from
`make_mock_images` are:

| Image | Result |
|---|---|
| `ok.jpg` | Successful verification |
| `spoof.jpg` | Fails the liveness check |
| `other.jpg` | Fails the match against the document |
| `timeout.jpg` | Simulates a provider outage |

## Tests

```bash
uv run pytest                 # unit, contract and integration tests
uv run ruff check . && uv run ruff format --check . && uv run mypy
```

Integration tests use `TEST_DATABASE_URL` if it is set. Otherwise they start an embedded Postgres
with `pgserver` (dev dependency; data in `.pgdata/`). `pgserver` ships wheels only up to Python
3.12: on a newer default Python, run the suite with `uv run --python 3.11 pytest`. Tests never use
the network or a real Sentry DSN; they capture telemetry with an in-memory transport
(`tests/support/sentry_capture.py`).

## Deploying to Vercel

1. Connect Neon, a **private** Blob store, Upstash Redis and QStash to the project. Set the variables
   from [`.env.example`](.env.example) with `AEROPASS_ADAPTERS=real`.
2. Run the migrations against Neon's **direct** URL:
   `DATABASE_URL_DIRECT=… uv run alembic upgrade head`.
3. Deploy (`vercel deploy`). The entrypoint is [`api/index.py`](api/index.py) and the region
   (`gru1`) is set in [`vercel.json`](vercel.json). Place Neon, Blob and Redis in the same region.
4. Create the dispatcher schedule (idempotent):
   `uv run python -m aeropass.tools.setup_qstash_schedule`, with `QSTASH_TOKEN` and `PUBLIC_BASE_URL`
   set.
5. Measure issuance latency (SC-002, p95 < 1 s):
   `uv run python -m aeropass.tools.bench_passes --base-url https://<deploy> --tokens tokens.txt`.

### Rotating the QR signing key

1. Generate a new key with a different `kid`:
   `uv run python -m aeropass.tools.gen_signing_key qr-2027`.
2. Update `QR_SIGNING_PRIVATE_KEY` and `QR_SIGNING_KID`.

Each QR lives at most 60 s, so the ones issued with the previous key expire on their own.
Checkpoints fetch the new key from `/.well-known/jwks.json`, which is cached for 5 min.

## Observability (Sentry)

Spec: [`specs/002-observabilidad-sentry/`](specs/002-observabilidad-sentry/spec.md). Errors,
traces, audit logs and business metrics go to the Sentry project `aeropass-back`. Without
`SENTRY_DSN` nothing is sent and the backend behaves exactly the same.

| Variable | Meaning |
|---|---|
| `SENTRY_DSN` | Project key of `aeropass-back`; empty turns observability off (local, CI, tests) |
| `SENTRY_ENVIRONMENT` | `prod`, `demo`, `dev` or `simulated`; the dashboard and alerts filter on it |
| `SENTRY_TRACES_SAMPLE_RATE` | Share of traced requests in [0, 1]: `0.2` in prod, `1.0` elsewhere. Errors, audit logs and metrics are always sent in full; `/health` is never traced |
| `VERCEL_GIT_COMMIT_SHA` | Set by Vercel; used as the Sentry release |

How it is wired (constitution 1.1.0, Principle VI):

- Business code only uses the hooks in
  [`observability/hooks.py`](src/aeropass/observability/hooks.py): `@traced`, `span()`,
  `@audited(describe=...)` and `emit_audit()`. Only
  [`adapters/observability/`](src/aeropass/adapters/observability) imports `sentry_sdk`.
- Event, metric and attribute names live in
  [`observability/telemetry_catalog.py`](src/aeropass/observability/telemetry_catalog.py). A new
  business metric on an audited step is a new `MetricRule` there. Anything not in the catalog never
  reaches Sentry, which
  [`test_telemetry_allowlist.py`](tests/contract/test_telemetry_allowlist.py) enforces.
- Every event, trace, breadcrumb, log and metric passes through `SentryPrivacyFilter`. No request
  bodies, local variables, query strings, auth headers or third-party exception messages leave the
  process ([contract](specs/002-observabilidad-sentry/contracts/privacy-filter.md)).
- On Vercel, [`api/index.py`](api/index.py) wraps the app in `FlushTelemetryMiddleware`, which
  sends queued telemetry after each response through `wait_until`. The passenger never waits for
  Sentry.
- `GET /health` (database + Redis, no details in the body) is polled every minute by a Sentry
  uptime monitor.
- The dashboard and alerts are configured by hand in Sentry. Their definitions and IDs are in
  [`contracts/dashboard-and-alerts.md`](specs/002-observabilidad-sentry/contracts/dashboard-and-alerts.md).

### Preparing the 15-minute demo

The dashboard's business widgets need data, and the hourly alerts cannot fire during a short
presentation. `seed_demo` runs the passenger flow in process (fake Clerk, Blob, Redis and QStash;
mock biometric provider) against a **non-prod** database and sends real telemetry:

```bash
SENTRY_DSN=<dsn> SENTRY_ENVIRONMENT=demo DATABASE_URL=<non-prod database, migrated> \
  uv run --python 3.11 python -m aeropass.tools.seed_demo --contingencia
```

1. Run it **at least one hour before** the presentation. It prints the self-service and
   auto-rejection rates the dashboard should show. It refuses to run with
   `SENTRY_ENVIRONMENT=prod`.
2. During the presentation, filter the dashboard by the `demo` environment:
   - W3: self-service rate (KR A1.2).
   - W4: auto-rejection by reason.
   - W6: pass latency.
   - W7: the `biometric` circuit opening (alert B3, triggered by `--contingencia`).
   - W8: inconclusive verifications.
3. To show an immediate alert live, run the tool again with `--solo-error`. It raises one
   unhandled error on a route that only exists inside the tool, and the B1 email arrives in
   under 2 minutes.
4. The self-service alert (B2) only evaluates `prod` over one hour. Show its configuration with
   the seeded data; do not claim it fired live.

## Out of scope for this repo

These parts are handled by other teams:

- Checkpoint validation (`validate`).
- Human agent console.
- Airline or GDS integration (`flights`).
- Fault injection (annex C experiments). The observability signals that verify each hypothesis
  touching this backend are already in place (spec 002, research §13).

This repo provides the extension points those parts need: `CredentialLifecycleService.consume`
(enforces RN-06), the JWKS, the event schemas, the `@traced`/`@audited` hooks and
`FlightCatalog`.

## Endpoints

Integration guide for the frontend. Interactive docs are served at `/docs` and the schema at
`/openapi.json`.

### General conventions

- **Authentication:** every `/v1/*` endpoint requires `Authorization: Bearer <Clerk session token>`
  (`await getToken()` in the Clerk SDK). In `fake` mode use `Bearer test:<user_id>`.
- **CORS:** all origins are allowed for now (`GET`, `POST`; `Authorization` and `Content-Type`
  headers; `Retry-After` is exposed). Restrict `allow_origins` in
  [`src/aeropass/main.py`](src/aeropass/main.py) once the frontend has a fixed domain.
- **Images:** JPEG, PNG or WebP. Max **4 MB per image** and **4.5 MB per request**.
- **Error format** (same for every endpoint):

  ```json
  { "codigo": "DOCUMENTO_VENCIDO", "mensaje": "...", "detalles": { "campos": ["..."] } }
  ```

### Endpoints used by the frontend

#### 🔐 Session and registration

| Method | Path | Purpose | Body | Success response |
|---|---|---|---|---|
| GET | `/v1/identity/me` | On app start: find out whether the user is registered and their status | — | `200` with `PasajeroResponse` |
| POST | `/v1/identity` | Register the passenger's document | `multipart/form-data`: `nombre_completo`, `tipo_documento` (`CC`/`CE`/`PASAPORTE`), `numero_documento`, `fecha_vencimiento` (`YYYY-MM-DD`), `foto_documento` (file) | `201` (created) or `200` (already existed) with `PasajeroResponse` |

Routing based on `GET /v1/identity/me`:

| Response | Screen |
|---|---|
| `404 PASAJERO_NO_REGISTRADO` | Registration |
| `estado = PENDIENTE_VERIFICACION` | Selfie |
| `estado = VERIFICADO` | Pass / QR |
| `estado = REQUIERE_REVISION_MANUAL` | Blocked / support |

#### 🤳 Biometric verification

| Method | Path | Purpose | Body | Success response |
|---|---|---|---|---|
| POST | `/v1/biometrics/verifications` | Send the selfie to be matched against the document | `multipart/form-data`: `selfie` (file) | `200` with `ResultadoVerificacionResponse` |

Handling by `resultado`:

- `EXITOSO` → go to the Pass screen.
- `FALLIDO` → show `motivo_fallo` (`LIVENESS`/`COMPARACION`) and `intentos_restantes`.
- `NO_CONCLUYENTE` → countdown of `reintentar_en_segundos` (30 s), then retry.

#### 🎫 Pass / QR

| Method | Path | Purpose | Body | Success response |
|---|---|---|---|---|
| POST | `/v1/passes` | Issue the QR and **refresh** it | JSON: `{ "codigo_vuelo": "AV123" }` (max 16 chars) | `201` with `PaseResponse` |
| GET | `/v1/passes/{credencial_id}` | Pass detail and state history | — | `200` with `DetallePaseResponse` |

Render the QR from the `token` field. Call `POST /v1/passes` again every `renovar_en_segundos`,
because the token expires in about 45 s (configurable between 30 and 60 s).

### Response types (TypeScript)

```ts
type EstadoPasajero = "PENDIENTE_VERIFICACION" | "VERIFICADO" | "REQUIERE_REVISION_MANUAL";
type EstadoCredencial = "EMITIDA" | "ACTIVA" | "CONSUMIDA" | "EXPIRADA" | "REVOCADA";

interface PasajeroResponse {
  id: string;
  nombre_completo: string;
  tipo_documento: "CC" | "CE" | "PASAPORTE";
  numero_documento_enmascarado: string;
  fecha_vencimiento: string;
  estado: EstadoPasajero;
  intentos_fallidos: number;
  identidad_id: string | null;
}

interface ResultadoVerificacionResponse {
  intento_id: string;
  resultado: "EXITOSO" | "FALLIDO" | "NO_CONCLUYENTE";
  motivo_fallo: "LIVENESS" | "COMPARACION" | null;
  score_liveness: number | null;
  score_comparacion: number | null;
  estado_pasajero: EstadoPasajero;
  intentos_restantes: number;
  identidad_id: string | null;
  reintentar_en_segundos: number | null;
}

interface PaseResponse {
  credencial_id: string;
  token: string;               // QR content
  codigo_vuelo: string;
  permisos: string[];
  estado: EstadoCredencial;
  emitida_at: string;
  expira_at: string;
  renovar_en_segundos: number;
}

interface DetallePaseResponse {
  credencial_id: string;
  codigo_vuelo: string;
  estado: EstadoCredencial;
  emitida_at: string;
  expira_at: string;
  historial: {
    estado_anterior: EstadoCredencial | null;
    estado_solicitado: EstadoCredencial;
    aceptada: boolean;
    motivo: string | null;
    created_at: string;
  }[];
}

interface ErrorResponse {
  codigo: string;
  mensaje: string;
  detalles?: { campos?: string[] };
}
```

### Errors the frontend must handle

| Code | HTTP | Where | What to show |
|---|---|---|---|
| `NO_AUTENTICADO` | 401 | All | Back to the Clerk login |
| `DATOS_INVALIDOS` | 422 | Registration, selfie, pass | Highlight the fields in `detalles.campos` |
| `DOCUMENTO_VENCIDO` | 422 / 403 | Registration / pass | "Your document has expired" |
| `DOCUMENTO_YA_REGISTRADO` | 409 | Registration | "That document is already in use" |
| `CUENTA_YA_REGISTRADA` | 409 | Registration | Go to `/me` |
| `PASAJERO_NO_REGISTRADO` | 404 | `/me`, selfie | Go to Registration |
| `ESTADO_NO_PERMITE_VERIFICACION` | 409 | Selfie | Refresh `/me` and redirect |
| `IMAGEN_DEMASIADO_GRANDE` | 413 | Registration, selfie | "Max 4 MB", compress the image |
| `FORMATO_NO_ADMITIDO` | 415 | Registration, selfie | "Use JPG, PNG or WebP" |
| `ALMACENAMIENTO_NO_DISPONIBLE` | 503 | Registration | "Please try again" |
| `IDENTIDAD_NO_ACTIVA` | 403 | Pass | Go to verification |
| `LIMITE_EMISION_EXCEDIDO` | 429 | Pass | Wait for the `Retry-After` header |
| `CREDENCIAL_NO_ENCONTRADA` | 404 | Pass detail | "Pass not found" |

### Full flow

```text
Clerk login
   └─ GET /v1/identity/me
        ├─ 404 ──────────────► POST /v1/identity ──┐
        ├─ PENDIENTE ◄─────────────────────────────┘
        │     └─ POST /v1/biometrics/verifications (retry on failure)
        └─ VERIFICADO
              └─ POST /v1/passes  ⟳ every renovar_en_segundos
                    └─ GET /v1/passes/{credencial_id} (detail)
```

### Endpoints not used by the frontend

| Method | Path | Who uses it |
|---|---|---|
| GET | `/.well-known/jwks.json` | Public (no auth). Only for a future scanner/checkpoint app that verifies the QR signature. |
| POST | `/internal/outbox/dispatch` | QStash only (signature required). Not shown in Swagger. |

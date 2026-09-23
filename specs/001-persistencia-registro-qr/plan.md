# Implementation Plan: Persistence of the registration, biometric verification and QR flow

**Branch**: `001-persistencia-registro-qr` | **Date**: 2026-09-23 | **Spec**: [spec.md](spec.md)

**Input**: Feature specification from `/specs/001-persistencia-registro-qr/spec.md`

## Summary

FastAPI backend deployed on Vercel that persists the four stages of passenger onboarding:
(1) document data in Neon Postgres and the document's face photo in a **private** Vercel Blob
store; (2) selfie in the same private store, evaluated on the server by a biometric provider
(liveness + comparison against the document photo) behind a circuit breaker, with the result and
the blob references in Postgres; (3) IdentidadDigital and a `credencial.emitida` event published
via QStash with a **transactional outbox**, so it is neither lost nor blocks the passenger;
(4) CredencialAcceso signed with Ed25519 (JWS), built with a Builder, with a single-use token in
Upstash Redis, immutable history in Postgres, a state machine (State pattern) that enforces
RN-06, automatic renewal and a limit of 30 issuances/min.

## Technical Context

**Language/Version**: Python 3.11+

**Primary Dependencies**: FastAPI, Pydantic v2 + pydantic-settings, SQLAlchemy 2.x (async) +
asyncpg, Alembic, `upstash-redis`, `upstash-ratelimit`, `qstash`, `clerk-backend-api`,
`PyJWT[crypto]` (EdDSA), `httpx` (Blob and biometric provider), `python-multipart`

**Storage**: Neon Postgres (relational, source of truth), private Vercel Blob (selfies and
document photo), Upstash Redis (single-use token, rate limit, circuit breaker state)

**Testing**: pytest, pytest-asyncio, httpx `AsyncClient` (ASGI), jsonschema; real Postgres
(docker or a Neon branch) for integration; in-memory doubles for every port

**Target Platform**: Vercel Functions, Python runtime (serverless, cold starts expected)

**Project Type**: web-service (backend-only REST API)

**Performance Goals**: pass issuance p95 < 1 s with a warm instance (SC-002); response with the
biometric provider down < 10 s p99 (SC-007); QR renewal before expiry in 99 % of cases (SC-008)

**Constraints**: request body ≤ 4.5 MB (each image ≤ 4 MB); no business state in memory between
requests; timeouts: biometric provider 4 s, Blob 3 s (selfie upload and reference photo download
in parallel), QStash 2 s → worst case for verification ~7 s (SC-007 < 10 s); no Postgres
transaction open during external calls; function region co-located with Neon, Blob and Upstash

**Scale/Scope**: *assumption to validate with the team*: single-airport pilot; ~5,000
passengers/day, peaks of ~200 passes open at once → ~7 issuances/s from automatic renewal;
~20 credentials per passenger per queue session

## Constitution Check

*GATE: Must pass before Phase 0 research. Re-check after Phase 1 design.*

| # | Principle / rule | Compliance in the design | Status |
|---|---|---|---|
| I | Canonical stack | FastAPI, Neon, Vercel Blob, Upstash Redis/QStash, Clerk, Vercel | ✅ |
| I | No API Gateway: every endpoint validates | `get_current_user` dependency (Clerk) per router; `/internal/*` validates the QStash signature | ✅ |
| I | QStash is HTTP with retries | Consumers deduplicate by `id`; own outbox for what QStash does not cover; `/internal/outbox/dispatch` is idempotent (`FOR UPDATE SKIP LOCKED`) | ✅ |
| I | No in-memory state | Breaker state in Redis; outbox in Postgres; only caches (JWKS) in process | ✅ |
| II | Only registration, biometrics, identity, QR | `validate`, agent console, `flights` and observability are not implemented | ✅ |
| II | Extension points ready | `consume()` RN-06, JWKS, event schema, `FlightCatalog` — see [extension-points.md](contracts/extension-points.md) | ✅ |
| III | SOLID / DI by abstraction | Ports in `ports/`; services receive ports via constructor; wiring only in `api/deps.py` | ✅ |
| IV | DRY | Biometric result, transition, signing and TTL rules live once in `domain/` | ✅ |
| V | Circuit breaker on unstable externals | Biometric provider and QStash wrapped; fallback `NO_CONCLUYENTE` / pending event | ✅ |
| VI | Observability hooks | `@traced` decorator (no-op by default) on services and adapters; no Sentry/OTel dependency | ✅ |
| Patterns | Factory, Builder, Singleton, Adapter, Facade, Proxy, Decorator, Observer, State | See the "Patterns → location" table | ✅ |
| Patterns | Facade with interface `verify_and_create_identity(pasajero, doc, selfie)` | Signature identical to the constitution | ✅ |
| Patterns | `credencial.emitida` and `validacion.fallida` schemas versioned in this repo | `contracts/events/*.v1.json` (the second one is produced by the checkpoint) | ✅ |
| Patterns | Strategy, Chain of Responsibility | Documented only (checkpoint, out of scope) | ✅ documented |

**Result**: passes with no violations. Re-evaluated after Phase 1 and after `/speckit-analyze`
(the Facade signature and the missing `validacion.fallida` schema were fixed).

### Patterns → location

| Pattern | Where |
|---|---|
| Factory | `adapters/biometrics/factory.py` (`BiometricProviderFactory`), `api/deps.py` (fakes vs real) |
| Builder | `domain/credential/builder.py` (`CredencialAccesoBuilder`: passenger → flight → permissions → ttl → signature → `build()` validates everything) |
| Singleton | `adapters/db/engine.py`, `adapters/redis/client.py`, `adapters/blob/client.py`, `adapters/qstash/client.py` (one instance per process, `functools.cache`) |
| Adapter | `adapters/biometrics/vision_adapter.py`, `adapters/blob/vercel_blob_storage.py` |
| Facade | `services/identity_verification_facade.py` — `verify_and_create_identity(pasajero, doc, selfie)`; `doc` = `DocumentoRegistrado` (data + photo reference), `selfie` = `ImageInput` |
| Proxy | `adapters/redis/token_proxy.py` (`RedisVerificationProxy`: checks `qr:{jti}` before Postgres) |
| Decorator | `observability/hooks.py` (`@traced`, `@audited`) on services and on the authentication/authorization dependency (`get_current_user`) |
| Observer | `ports/event_publisher.py` + outbox + QStash URL group |
| State | `domain/credential/states.py` (one class per state with `transition_to()`) |
| Circuit Breaker | `adapters/resilience/circuit_breaker.py` + `RedisBreakerStateStore` |

## Project Structure

### Documentation (this feature)

```text
specs/001-persistencia-registro-qr/
├── plan.md
├── research.md
├── data-model.md
├── quickstart.md
├── contracts/
│   ├── openapi.yaml
│   ├── extension-points.md
│   └── events/
│       ├── credencial.emitida.v1.json
│       └── validacion.fallida.v1.json   # produced by the checkpoint; defined here
├── checklists/requirements.md
└── tasks.md
```

### Source Code (repository root)

```text
api/
└── index.py                         # Vercel entrypoint: from aeropass.main import app

src/aeropass/
├── main.py                          # create_app(), routers, error handlers
├── config.py                        # Settings (pydantic-settings)
├── domain/                          # pure business rules, no I/O
│   ├── errors.py
│   ├── passenger.py                 # Pasajero, EstadoPasajero, 3-attempt rule
│   ├── verification.py              # BiometricResult, thresholds, EXITOSO/FALLIDO rule
│   ├── identity.py
│   ├── events.py                    # DomainEvent, CredencialEmitidaV1
│   └── credential/
│       ├── states.py                # State: Emitida, Activa, Consumida, Expirada, Revocada
│       ├── credential.py            # CredencialAcceso aggregate
│       ├── builder.py               # CredencialAccesoBuilder
│       └── signing.py               # CredentialSigner / CredentialVerifier (Ed25519)
├── ports/                           # abstractions (Protocol)
│   ├── repositories.py              # Passenger/Attempt/Identity/Credential/Outbox repos + UnitOfWork
│   ├── media_storage.py
│   ├── biometric_provider.py
│   ├── token_store.py
│   ├── rate_limiter.py
│   ├── event_publisher.py
│   ├── flight_catalog.py
│   └── clock.py
├── services/
│   ├── registration_service.py
│   ├── biometric_verification_service.py
│   ├── identity_service.py
│   ├── identity_verification_facade.py
│   ├── pass_issuance_service.py
│   ├── credential_lifecycle_service.py   # expire/revoke/consume (checkpoint extension)
│   └── outbox_dispatcher.py
├── adapters/
│   ├── db/            # engine.py (Singleton), orm.py, repositories.py, unit_of_work.py
│   ├── blob/          # client.py (Singleton), vercel_blob_storage.py
│   ├── redis/         # client.py (Singleton), token_store.py, token_proxy.py, rate_limiter.py, breaker_state.py
│   ├── qstash/        # client.py, publisher.py, receiver.py
│   ├── biometrics/    # factory.py, vision_adapter.py, mock_adapter.py
│   ├── resilience/    # circuit_breaker.py
│   ├── auth/          # clerk.py
│   ├── flights/       # format_only_catalog.py
│   └── fakes/         # in-memory doubles of each external service (tests and AEROPASS_ADAPTERS=fake)
├── observability/
│   └── hooks.py                     # @traced / @audited (no-op until the other team wires them)
├── api/
│   ├── deps.py                      # dependency composition (the only place that instantiates concretes)
│   ├── schemas.py                   # Pydantic request/response models
│   └── routers/                     # identity.py, biometrics.py, passes.py, wellknown.py, internal.py
└── tools/
    └── gen_signing_key.py

migrations/                          # Alembic (enums, tables, partial indexes, trigger)
tests/
├── unit/
├── integration/
└── contract/

pyproject.toml
vercel.json
```

**Structure Decision**: a single backend project with a light hexagonal architecture: `domain/`
(pure rules), `ports/` (abstractions), `services/` (use cases), `adapters/` (concrete
implementations) and `api/` (FastAPI). Only `api/deps.py` knows the concrete classes, which
satisfies SOLID/DI.

**Adapter modes** (`AEROPASS_ADAPTERS`):

- `real`: Clerk, Vercel Blob, Upstash Redis, QStash and the provider chosen by
  `BIOMETRIC_PROVIDER`.
- `fake`: replaces **only the external services** (Clerk, Blob, Redis, QStash) with the doubles
  in `adapters/fakes/`. Postgres is **always** real (local or a Neon branch), because the SQL
  constraints (partial indexes, CHECK, trigger) are part of the logic.
- `InMemoryUnitOfWork` is used only in service unit tests, never at runtime.

## Key flows

**Registration** (`RegistrationService`):

1. Validate data and photo (type by header and byte signature, ≤ 4 MB) → 2. Resolve idempotency
(same account + same document → 200 without uploading anything) → 3. Upload the photo to
`documentos/{pasajero_id}/…` (on failure: 503, nothing persisted) → 4. Transaction: insert the
passenger with the reference → commit (if a unique constraint fails due to a race, it is
translated into the domain error and the uploaded photo is left orphaned and identifiable).

**Selfie verification** — `IdentityVerificationFacade.verify_and_create_identity(pasajero, doc,
selfie)`. The router resolves `pasajero` and `doc` from the authenticated account:

1. Validate the selfie's type and size → 2. `pasajero.assert_can_verify()` **without a lock** →
3. In parallel (`asyncio.gather`, 3 s): upload the selfie to private Blob and download the
reference photo from `doc` (if either fails: 503, no attempt) → 4. `BiometricProvider.evaluate(selfie,
referencia)` through the circuit breaker, 4 s (if it fails or is open: `NO_CONCLUYENTE`) →
5. Domain rule → 6. Short transaction: lock the passenger (`FOR UPDATE`), **revalidate** its
status (if it changed: `EstadoNoPermiteVerificacion`, the selfie is left orphaned), record the
attempt + passenger status + (if EXITOSO) IdentidadDigital + outbox row → commit →
7. Immediate publish to QStash (best effort, 2 s) → 8. Response.

**Pass issuance** (`PassIssuanceService`):

1. Rate limit (Redis) → 2. Transaction: lock the passenger; validate active identity and valid
document; revoke in Postgres the live credential for the same flight (`RENOVACION` transition) →
3. `CredencialAccesoBuilder` → Ed25519 signature → 4. Insert credential `EMITIDA` + transition →
5. `SET qr:{jti} ACTIVA EX ttl NX` → 6. Transition `EMITIDA → ACTIVA` → commit →
7. **After the commit**: `DEL qr:{previous_jti}` (if it fails, it expires on its own in ≤ 60 s) →
8. Response with `renovar_en_segundos = ttl − 5`. If step 5 fails, the credential becomes
`REVOCADA`, it is committed and a 503 is returned.

**Credential transitions**: the aggregate's methods (`activar`, `consumir`, `expirar`,
`revocar`) return `TransitionResult(aceptada, estado_actual)` and **never raise** for an invalid
transition; that way the rejection row is committed in the same transaction (FR-019) and the
service translates the result into the response after the commit.

## Complexity Tracking

No constitution violations to justify.

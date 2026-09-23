# Research: Persistence of the registration, biometric verification and QR flow

**Feature**: `001-persistencia-registro-qr` | **Date**: 2026-09-23

Each decision follows the Decision / Rationale / Alternatives considered format. No
`NEEDS CLARIFICATION` remain in the plan's Technical Context.

## R1. Accessing Neon Postgres from serverless

- **Decision**: SQLAlchemy 2.x in async mode with the `asyncpg` driver, against Neon's **pooled**
  endpoint (PgBouncer in transaction mode). A single `AsyncEngine` per process (Singleton) with
  `pool_size=1`, `max_overflow=4`, `pool_pre_ping=True`, `pool_recycle=300` and
  `connect_args={"statement_cache_size": 0}` + `prepared_statement_cache_size=0` in the URL
  (mandatory with PgBouncer in transaction mode). Migrations with Alembic using Neon's **direct**
  (non-pooled) URL.
- **Rationale**: the ORM provides units of work and row locks (`SELECT … FOR UPDATE`) needed to
  count attempts and revoke credentials without races; asyncpg is the fastest async driver.
  Neon's pooler absorbs Vercel's many ephemeral instances; the Singleton engine reuses the
  connection while the instance is warm and `pool_pre_ping` recovers broken connections after a
  freeze.
- **Alternatives considered**: plain asyncpg (less code, but would repeat mapping and units of
  work — violates DRY); Neon's serverless HTTP driver (no mature official Python client and it
  loses interactive transactions); `NullPool` (a new connection per request: +30–80 ms).

## R2. Selfie storage (Vercel Blob)

- **Decision**: Vercel Blob store with **private** access (`access: private`) for the selfie
  (`selfies/{pasajero_id}/{intento_id}-{suffix}.{ext}`) and for the document's face photo
  (`documentos/{pasajero_id}/rostro-{suffix}.{ext}`), with a **3 s** timeout per operation. Read
  only from the server with `BLOB_READ_WRITE_TOKEN` (or `VERCEL_OIDC_TOKEN`). Access goes behind
  the `MediaStorage` port; the `VercelBlobStorage` implementation uses Vercel's Python SDK if it
  exposes Blob `put/get/delete`, and otherwise a thin `httpx` layer over the Blob HTTP API. The
  HTTP client is a Singleton.
- **Rationale**: Vercel's documentation confirms private storage with Bearer token
  authentication, which satisfies FR-010. The port isolates the uncertainty about the Python
  SDK's maturity.
- **Alternatives considered**: public store with an unguessable URL (security by obscurity,
  does not satisfy FR-010); direct upload from the client with a client token (the server would
  have to trust a URL sent by the client, and the photo would not go through server validation).

## R3. Maximum image size

- **Decision**: max **4 MB** per image (selfie or document photo; JPEG, PNG or WebP), validated
  by the `Content-Length` header, `Content-Type` and byte signature before uploading it. The
  spec's edge case was adjusted (previously 5 MB).
- **Rationale**: the request body of a Vercel Function is limited to 4.5 MB; with the multipart
  envelope, 5 MB does not fit. A selfie compressed on the client typically weighs less than 1 MB.
- **Alternatives considered**: direct upload from the client to Blob (see R2, discarded).

## R4. Biometric provider (liveness + face match)

- **Decision**: port `BiometricProvider.evaluate(selfie: ImageInput, referencia: ImageInput) ->
  BiometricResult` (liveness score, match score, provider), where `referencia` is the document's
  face photo stored at registration (FR-001a). Implementations: `VisionProviderAdapter` (generic
  HTTP via `httpx`, translates the provider's response into the domain model) and
  `MockBiometricAdapter` (deterministic: decides by an ASCII marker `MOCK:ok|spoof|other|timeout`
  embedded in the selfie bytes; no marker → `ok`). They are chosen with
  `BiometricProviderFactory` according to `BIOMETRIC_PROVIDER=mock|vision`. **4 s** timeout per
  call.
- **Latency budget (SC-007, < 10 s)**: uploading the selfie and downloading the reference photo
  run in parallel (`asyncio.gather`, 3 s max) and then the provider (4 s max): worst case ~7 s +
  database, with headroom for a cold start.
- **Rationale**: Factory + Adapter required by the constitution; the real provider is not
  contracted yet, so the mock allows progress and testing.
- **Alternatives considered**: coupling to a concrete provider (Rekognition, Azure Face) —
  premature; WASM verification on the client — discarded during clarification (it is evaluated
  on the server).

## R5. Circuit Breaker

- **Decision**: in-house `CircuitBreaker` implementation (CLOSED / OPEN / HALF_OPEN states) with
  pluggable state storage: `RedisBreakerStateStore` (Upstash Redis, shared between instances) in
  production and `InMemoryBreakerStateStore` in tests. Default parameters: opens with 5 failures
  in a 60 s window, stays open 30 s, and in HALF_OPEN lets a single probe call through. Applied to
  the biometric provider and the QStash publisher. If the breaker is open, the biometric provider
  returns `NO_CONCLUYENTE` instantly and the publisher leaves the event pending in the outbox.
- **Rationale**: the libraries (`pybreaker`, `aiobreaker`, `purgatory`) keep the state in memory
  or in redis-py; in serverless in-memory state is lost on every cold start (constitution,
  Principle V). An in-house breaker with a state port fits in ~100 lines and is fully testable.
- **Alternatives considered**: `purgatory` with a Redis backend (uses the Redis protocol and not
  Upstash's REST client; adds a second way of connecting to Redis).

## R6. Upstash Redis: single-use token and issuance limit

- **Decision**: `upstash-redis` client (async, over HTTP REST) as a Singleton.
  - Token: key `qr:{jti}` = `ACTIVA`, with `SET … EX <ttl> NX`, where the TTL equals the
    credential's expiration.
  - Revocation: `DEL qr:{jti}`.
  - Consumption (extension point for the checkpoint): atomic Lua script: if the value is
    `ACTIVA`, it changes it to `CONSUMIDA` with `KEEPTTL` and returns 1; otherwise it returns 0
    (RN-06).
  - Issuance limit: `upstash-ratelimit` with a sliding window of 30 issuances / 60 s per
    `pasajero_id`.
- **Rationale**: the REST client keeps no open connections, ideal for serverless; the Lua script
  guarantees atomic consumption even if two reads arrive at once.
- **Alternatives considered**: `GETDEL` (leaves no trace that the credential was consumed — the
  distinction between consumed and expired is lost); redis-py over TLS (persistent connections
  that do not survive the instance freeze).

## R7. Publishing `credencial.emitida` without losing events (QStash)

- **Decision**: **Transactional outbox**.
  1. The IdentidadDigital and the `outbox_eventos` row (status `PENDIENTE`) are inserted in the
     **same transaction**.
  2. After the commit, an immediate publish is attempted with `qstash`
     (`AsyncQStash.message.publish_json`) with a 2 s timeout, through the circuit breaker, using
     `deduplication_id = evento.id`. On success the row moves to `ENTREGADO`; on failure it stays
     `PENDIENTE` and the response to the passenger is not affected.
  3. A **QStash schedule** (every minute) calls `POST /internal/outbox/dispatch`, signed by
     QStash, which retries pending events with exponential backoff `min(2^intentos, 180)` s
     (worst case after recovery: 180 s + 60 s of the schedule = 4 min, within SC-006) and marks
     expired credentials as `EXPIRADA`.
- **Rationale**: guarantees FR-013 (the event is not lost) and SC-006 (delivery in less than
  5 min after QStash recovers) without relying on background tasks after the response, which
  Vercel's Python runtime does not guarantee. QStash deduplication and the event `id` let
  consumers process it only once.
- **Alternatives considered**: FastAPI `BackgroundTasks` (the function may freeze on responding
  and lose the send); Vercel Cron (on the Hobby plan it is only daily; the QStash schedule keeps
  everything inside the constitution's stack).
- **Note on the event name**: the spec (FR-012) publishes `credencial.emitida` when the
  IdentidadDigital is created. That name is kept; the content includes
  `tipo_credencial: "IDENTIDAD_DIGITAL"` so it is not confused with the issuance of each QR,
  which does **not** produce an event (at 30–60 s per QR with automatic renewal it would be
  noise).

## R8. Signing the CredencialAcceso

- **Decision**: compact **JWS with EdDSA (Ed25519)** token via `PyJWT[crypto]`. Claims: `jti`
  (credential id), `sub` (pasajero_id), `flt` (flight code), `perms`, `iat`, `exp`, `kid`.
  Private key in the `QR_SIGNING_PRIVATE_KEY` environment variable (PEM); the public key is
  exposed at `GET /.well-known/jwks.json`. The QR encodes the JWS.
- **Rationale**: asymmetric signing lets the checkpoint (another team) verify the signature
  without sharing secrets and also in contingency/offline mode (Strategy, constitution). Ed25519
  produces short signatures (64 bytes), suitable for a readable QR.
- **Alternatives considered**: HMAC-SHA256 (forces sharing the secret with every checkpoint);
  RSA (256+ byte signatures, denser QR).

## R9. Authentication with Clerk

- **Decision**: FastAPI dependency `get_current_user` that validates the Clerk session token with
  `clerk-backend-api` (`authenticate_request`) and returns `AuthenticatedUser(clerk_user_id)`. The
  JWKS is cached per process (it is a cache, not business state). `/internal/*` does not use
  Clerk: the QStash signature (`Upstash-Signature`) is validated with `qstash.Receiver`.
- **Rationale**: official SDK; without an API Gateway, each router declares its security
  dependency.
- **Alternatives considered**: PyJWT + manual JWKS (valid as a fallback; more in-house code).

## R10. Concurrency and integrity

- **Decision**:
  - Document uniqueness: `UNIQUE (tipo_documento, numero_documento)`.
  - One active IdentidadDigital per passenger: partial unique index `WHERE estado = 'ACTIVA'`.
  - One non-final credential per passenger and flight: partial unique index on
    `(pasajero_id, codigo_vuelo) WHERE estado IN ('EMITIDA','ACTIVA')`. Renewal revokes the
    previous one and creates the new one in the **same transaction**, with `SELECT … FOR UPDATE`
    on the passenger.
  - Failed attempts: the counter is incremented with the passenger locked (`FOR UPDATE`). The
    lock is taken **only** in the final transaction that records the attempt, never while the
    image is uploaded or the provider is called (avoids holding Neon pooler connections for up
    to ~7 s); inside that transaction the passenger's status is revalidated.
  - Consumption (RN-06): `UPDATE … SET estado='CONSUMIDA' WHERE id=:id AND estado='ACTIVA'` plus
    the Lua script in Redis; if neither applies, the transition is rejected and recorded.
  - Rejected transitions: the aggregate's methods return a `TransitionResult` instead of
    raising, so the UnitOfWork rollback does not erase the rejection row (FR-019).
  - Redis after commit: `DEL qr:{jti}` of the revoked credential runs after the commit; if it
    fails, the token expires on its own in ≤ 60 s and Postgres already marks it `REVOCADA`.
  - Immutable history: `transiciones_credencial` only allows `INSERT` (a trigger rejects
    `UPDATE` and `DELETE`).
- **Rationale**: database constraints are the last line of defense against races between
  serverless instances.

## R11. Tests

- **Decision**: `pytest` + `pytest-asyncio` + `httpx.AsyncClient` (ASGI).
  - **Unit**: domain (State, Builder, attempt rules) and services with in-memory doubles of all
    ports (`src/aeropass/adapters/fakes/`, also used with `AEROPASS_ADAPTERS=fake`).
  - **Integration**: against a real Postgres (local `postgres:16` container or a Neon branch via
    `TEST_DATABASE_URL`), with fake Redis/Blob/QStash.
  - **Contract**: validation of responses against `contracts/openapi.yaml` and of events against
    `contracts/events/*.json` (`jsonschema`).
- **Rationale**: port fakes make tests fast and deterministic; SQL constraints (partial indexes,
  trigger) can only be tested with real Postgres.

## R12. Deployment and packaging

- **Decision**: `pyproject.toml` managed with `uv`; FastAPI entrypoint `api/index.py`
  (re-exports `app` from `aeropass.main`); `vercel.json` rewriting all routes to `api/index.py`
  and `excludeFiles` for `tests/**`. The function region is set next to the Neon region (e.g.
  `gru1` ↔ `aws-sa-east-1` for users in Colombia), as are the regions of the Blob store and the
  Upstash database.
- **Rationale**: every cross-region hop adds 100+ ms, which would compromise SC-002.

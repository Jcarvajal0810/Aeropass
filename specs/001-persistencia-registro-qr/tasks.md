---

description: "Task list for 001-persistencia-registro-qr"
---

# Tasks: Persistence of the registration, biometric verification and QR flow

**Input**: Design documents from `/specs/001-persistencia-registro-qr/`

**Prerequisites**: plan.md, spec.md, research.md, data-model.md, contracts/, quickstart.md

**Tests**: includes the tests required by the spec's success criteria (SC-002 performance,
SC-004 concurrency, SC-006 outbox recovery), FR-019 (recorded rejections) and the key tests listed
in `quickstart.md`. Write them first and verify they fail before implementing.

**Organization**: Tasks are grouped by user story to enable independent implementation and testing of each story.

**Revision**: regenerated after `/speckit-analyze` (findings U1, C1, C2, I1, I2, P1, P2, G1, A1,
T2, D1, D2, D3, S1 fixed).

## Format: `[ID] [P?] [Story] Description`

- **[P]**: Can run in parallel (different files, no dependencies)
- **[Story]**: Which user story this task belongs to (US1–US4)
- Paths are relative to the repository root. Python package: `src/aeropass/`

## Conventions for all tasks

- Business rules only in `src/aeropass/domain/` (DRY). Services receive ports
  (`typing.Protocol` in `src/aeropass/ports/`) via constructor; only `src/aeropass/api/deps.py`
  instantiates concrete classes (SOLID).
- Doubles of external services in `src/aeropass/adapters/fakes/` (tests and
  `AEROPASS_ADAPTERS=fake`). Postgres is always real; `InMemoryUnitOfWork` is only used in service
  unit tests (plan.md, "Adapter modes").
- No Postgres transaction stays open during a call to Blob, the biometric provider or QStash.
- The credential's transition methods return `TransitionResult` and never raise for an invalid
  transition (data-model.md).
- Every public method of a service and external adapter carries `@traced("<name>")` from
  `src/aeropass/observability/hooks.py`.
- Never log the full document number, scores alongside a name, or image bytes.

---

## Phase 1: Setup (Shared Infrastructure)

**Purpose**: Project initialization and basic structure

- [X] T001 Create the package layout from plan.md ("Source Code" tree): `src/aeropass/{domain/credential,ports,services,adapters/{db,blob,redis,qstash,biometrics,resilience,auth,flights,fakes},observability,api/routers,tools}`, `tests/{unit,integration,contract}`, `migrations/versions`, `api/`, each Python package with an empty `__init__.py`
- [X] T002 Create `pyproject.toml` (uv, `requires-python = ">=3.11"`, src layout, package `aeropass`) with runtime deps `fastapi`, `pydantic>=2`, `pydantic-settings`, `sqlalchemy[asyncio]>=2`, `asyncpg`, `alembic`, `upstash-redis`, `upstash-ratelimit`, `qstash`, `clerk-backend-api`, `PyJWT[crypto]`, `httpx`, `python-multipart`, `uuid-utils` (UUIDv7), `pillow` (only for the mock-image generator) and dev deps `pytest`, `pytest-asyncio` (`asyncio_mode = "auto"`), `jsonschema`, `pyyaml`, `ruff`, `mypy`; configure `[tool.ruff]` (line-length 100) and `[tool.pytest.ini_options]` (`testpaths = ["tests"]`, marker `integration`); run `uv lock`
- [X] T003 [P] Create Vercel entrypoint `api/index.py` (`from aeropass.main import app`) and `vercel.json` with a rewrite of `/(.*)` to `/api/index`, `functions["api/index.py"]` with `maxDuration: 30` and `excludeFiles: "{tests/**,specs/**,migrations/**}"`, and `regions: ["gru1"]` (co-located with Neon `aws-sa-east-1`, research R12)
- [X] T004 [P] Create `.env.example` listing every variable in the "Environment variables" table of `specs/001-persistencia-registro-qr/quickstart.md` plus `AEROPASS_ADAPTERS=real|fake`, `TEST_DATABASE_URL`, `VISION_PROVIDER_URL`, `VISION_PROVIDER_API_KEY`, `BLOB_TIMEOUT_SECONDS=3`, `BIOMETRIC_TIMEOUT_SECONDS=4`, `QSTASH_TIMEOUT_SECONDS=2`; ensure `.gitignore` ignores `.env*` except `.env.example`

---

## Phase 2: Foundational (Blocking Prerequisites)

**Purpose**: Core infrastructure that MUST be complete before ANY user story can be implemented

**⚠️ CRITICAL**: No user story work can begin until this phase is complete

- [X] T005 Implement `Settings` (pydantic-settings, reads env) in `src/aeropass/config.py` with all variables from `.env.example`, validators `QR_TTL_SECONDS` in 30–60 (default 45), thresholds in 0–1 (default 0.80), `BIOMETRIC_PROVIDER` in {`mock`,`vision`}, `AEROPASS_ADAPTERS` in {`real`,`fake`}, timeouts (Blob 3 s, biometric 4 s, QStash 2 s); expose cached `get_settings()`
- [X] T006 [P] Define every enum from data-model.md ("Enumerations") as `StrEnum` in `src/aeropass/domain/enums.py` (single source used by domain, ORM and API)
- [X] T007 [P] Implement domain error hierarchy in `src/aeropass/domain/errors.py`: base `DomainError(codigo, mensaje, detalles)` and one subclass per `Error.codigo` in `contracts/openapi.yaml` (`DatosInvalidos`, `DocumentoVencido`, `DocumentoYaRegistrado`, `CuentaYaRegistrada`, `PasajeroNoRegistrado`, `EstadoNoPermiteVerificacion`, `ImagenDemasiadoGrande`, `FormatoNoAdmitido`, `AlmacenamientoNoDisponible` (with `retry_after`), `IdentidadNoActiva`, `LimiteEmisionExcedido` (with `retry_after`), `CredencialNoEncontrada`, `NoAutenticado`)
- [X] T008 [P] Implement `Clock` protocol in `src/aeropass/ports/clock.py` and `SystemClock` (UTC) plus `FakeClock` (settable/advanceable) in `src/aeropass/adapters/clock.py`
- [X] T009 [P] Implement observability hooks in `src/aeropass/observability/hooks.py`: decorators `@traced(name)` and `@audited(event)` supporting sync/async functions, delegating to a module-level registry of hook callables (`register_span_hook`, `register_audit_hook`) that is empty (no-op) by default — constitution Principle VI
- [X] T010 [P] Implement generic `CircuitBreaker` in `src/aeropass/adapters/resilience/circuit_breaker.py`: states CLOSED/OPEN/HALF_OPEN, `failure_threshold=5`, `window_seconds=60`, `open_seconds=30`, one trial call in HALF_OPEN; `async call(fn, *args, timeout)` raising `CircuitOpenError` when open; per-call timeout via `asyncio.wait_for` (timeouts count as failures); state persisted through a `BreakerStateStore` protocol (`get(name)`, `save(name, state)`) with `InMemoryBreakerStateStore`; uses `Clock` (research R5)
- [X] T011 [P] Implement Singleton Upstash Redis client in `src/aeropass/adapters/redis/client.py` (`functools.cache` factory returning `upstash_redis.asyncio.Redis` from settings)
- [X] T012 Implement `RedisBreakerStateStore` in `src/aeropass/adapters/redis/breaker_state.py` storing a hash at `cb:{name}` with TTL 600 s (data-model.md "Upstash Redis") (depends on T010, T011)
- [X] T013 Implement Singleton async engine and session factory in `src/aeropass/adapters/db/engine.py` per research R1 (`asyncpg`, `pool_size=1`, `max_overflow=4`, `pool_pre_ping=True`, `pool_recycle=300`, `connect_args={"statement_cache_size": 0}`, `prepared_statement_cache_size=0`)
- [X] T014 Create SQLAlchemy `DeclarativeBase` with naming convention and PG enum types mapped from `domain/enums.py` (`create_type=False`) in `src/aeropass/adapters/db/orm.py`
- [X] T015 Define `UnitOfWork` protocol (async context manager, `commit()`, `rollback()`, repository attributes added per story) in `src/aeropass/ports/repositories.py` and `SqlAlchemyUnitOfWork` in `src/aeropass/adapters/db/unit_of_work.py` (one `AsyncSession` per unit, rollback on unhandled exception)
- [X] T016 Initialize Alembic: `alembic.ini`, async `migrations/env.py` using `DATABASE_URL_DIRECT` and `Base.metadata`, and `migrations/versions/0000_enums.py` creating all PG enum types from data-model.md
- [X] T017 [P] Implement Clerk dependency `get_current_user() -> AuthenticatedUser(clerk_user_id)` in `src/aeropass/adapters/auth/clerk.py` using `clerk-backend-api` `authenticate_request` (research R9), decorated with `@audited("auth.authenticate")` (constitution Decorator on authorization logic), raising `NoAutenticado`; and `FakeAuth` in `src/aeropass/adapters/fakes/auth.py` accepting `Authorization: Bearer test:<user_id>`
- [X] T018 [P] Define image input and validation in `src/aeropass/domain/images.py` (single source for selfie and document photo, DRY): `ImageInput(data: bytes, content_type: str)`, `validate_image(data, declared_content_type)` checking size ≤ 4 MB (`ImagenDemasiadoGrande`) and that both the declared type and the magic bytes are JPEG/PNG/WebP (`FormatoNoAdmitido`), `extension_for(content_type)`
- [X] T019 [P] Define `MediaStorage` protocol (`put_private(pathname, image: ImageInput) -> StoredMedia(url, pathname)`, `get(pathname) -> ImageInput`, `delete(pathname)`, raising `MediaUnavailable`) in `src/aeropass/ports/media_storage.py`
- [X] T020 [P] Implement Singleton Blob HTTP client in `src/aeropass/adapters/blob/client.py` and `VercelBlobStorage` (private access, random suffix, `BLOB_TIMEOUT_SECONDS` per operation, auth with `BLOB_READ_WRITE_TOKEN`; use the Vercel Python SDK Blob API if available, otherwise the Blob HTTP API via `httpx`, research R2; maps network/5xx/timeout errors to `MediaUnavailable`) in `src/aeropass/adapters/blob/vercel_blob_storage.py`
- [X] T021 [P] Implement `InMemoryMediaStorage` (stores bytes by pathname, `fail_next_put` / `fail_next_get` switches) in `src/aeropass/adapters/fakes/media_storage.py`
- [X] T022 [P] Implement mock-image helpers: `make_image(marker: str | None, fmt="JPEG") -> bytes` that produces a small valid image with the ASCII marker `MOCK:<marker>` in a JPEG COM segment / PNG tEXt chunk, in `src/aeropass/adapters/fakes/images.py`, and CLI `python -m aeropass.tools.make_mock_images <dir>` writing `ok.jpg`, `spoof.jpg`, `other.jpg`, `timeout.jpg`, `documento.jpg` in `src/aeropass/tools/make_mock_images.py`
- [X] T023 Implement app factory `create_app()` and module-level `app` in `src/aeropass/main.py`: registers routers (added per story), maps `DomainError` subclasses to HTTP status per `contracts/openapi.yaml` with body `{codigo, mensaje, detalles}` (adds `Retry-After` when present), maps `RequestValidationError` to 422 `DATOS_INVALIDOS`; add shared `ErrorResponse` model in `src/aeropass/api/schemas.py`
- [X] T024 Implement composition root in `src/aeropass/api/deps.py` following plan.md "Adapter modes": FastAPI dependencies for settings, clock, current user (Clerk vs `FakeAuth`), `SqlAlchemyUnitOfWork` factory (always real Postgres), breaker state store (Redis vs in-memory), media storage (`VercelBlobStorage` vs `InMemoryMediaStorage` as a process-level singleton in fake mode); this is the ONLY module that imports concrete adapters
- [X] T025 Create test fixtures in `tests/conftest.py`: `db_url` from `TEST_DATABASE_URL` (skip integration tests if absent), session-scoped `alembic upgrade head`, per-test `TRUNCATE … CASCADE`, `app` built with `AEROPASS_ADAPTERS=fake` and dependency overrides exposing the fake instances to tests, `client` (`httpx.AsyncClient` with `ASGITransport`), `auth_headers(user_id)` helper, `fake_clock`, `image(marker)` fixture using `make_image`
- [X] T026 [P] Create OpenAPI contract helper in `tests/contract/openapi_helper.py`: loads `specs/001-persistencia-registro-qr/contracts/openapi.yaml`, `assert_matches(response, path, method, status)` validating the JSON body with `jsonschema` against the resolved response schema
- [X] T027 [P] Unit tests in `tests/unit/test_circuit_breaker.py` (opens after 5 failures within 60 s, fails fast while open, HALF_OPEN after 30 s allows one call, closes on success, reopens on failure, timeouts count as failures; uses `FakeClock`) and in `tests/unit/test_images.py` (size limit, declared type vs magic bytes mismatch rejected, JPEG/PNG/WebP accepted)

**Checkpoint**: Foundation ready - user story implementation can now begin

---

## Phase 3: User Story 1 - Identity document registration (Priority: P1) 🎯 MVP

**Goal**: the authenticated passenger registers their document data and photo (photo in the private store, only the reference in Postgres) and is left in `PENDIENTE_VERIFICACION`.

**Independent Test**: `POST /v1/identity` (multipart with `foto_documento`) with a valid document → 201 and `GET /v1/identity/me` returns the passenger in `PENDIENTE_VERIFICACION`; resubmission → 200 same id; expired document → 422; same document from another account → 409 `DOCUMENTO_YA_REGISTRADO`; another document from the same account → 409 `CUENTA_YA_REGISTRADA`; store failure → 503 with no passenger.

### Tests for User Story 1 ⚠️

- [X] T028 [P] [US1] Unit tests for passenger rules in `tests/unit/test_passenger.py`: document number normalization (uppercase, strip spaces/dots/dashes), 4–20 alphanumerics, expiry date < today rejected, masked number shows last 4 chars, new passenger state `PENDIENTE_VERIFICACION`, `intentos_fallidos = 0`, requires a document photo reference
- [X] T029 [P] [US1] Contract tests in `tests/contract/test_identity_contract.py` for `POST /v1/identity` (201, 200, 409 both codes, 413, 415, 422, 503) and `GET /v1/identity/me` (200, 404, 401) using `openapi_helper.assert_matches`
- [X] T030 [P] [US1] Integration tests in `tests/integration/test_registration.py` for spec US1 acceptance scenarios 1–5 plus edge cases: missing fields or missing photo (422 listing fields in `detalles`, nothing persisted, nothing uploaded), photo storage failure (503, no passenger row), and that `pasajeros` stores only the photo pathname/URL while the fake media storage holds the bytes

### Implementation for User Story 1

- [X] T031 [P] [US1] Implement `DocumentoRegistrado` value object (tipo, numero normalizado, nombre, fecha_vencimiento, `foto: StoredMedia`, `is_valid_on(date)`) and `Pasajero` entity with factory `Pasajero.registrar(...)` enforcing the rules tested in T028 in `src/aeropass/domain/passenger.py`
- [X] T032 [P] [US1] Add `PasajeroRow` ORM mapping (table `pasajeros`, columns per data-model.md including `foto_documento_blob_pathname`/`_url`, `UNIQUE(tipo_documento, numero_documento)`, `UNIQUE(clerk_user_id)`, CHECK `intentos_fallidos BETWEEN 0 AND 3`) in `src/aeropass/adapters/db/orm.py`
- [X] T033 [US1] Create migration `migrations/versions/0001_pasajeros.py` for table `pasajeros` (depends on T032)
- [X] T034 [US1] Add `PassengerRepository` protocol (`get_by_clerk_user(id)`, `get_by_document(tipo, numero)`, `get_for_update(id)`, `add(p)`, `save(p)`, `list_by_estado(estado)`) and `UnitOfWork.passengers` in `src/aeropass/ports/repositories.py`
- [X] T035 [P] [US1] Implement `SqlPassengerRepository` (row↔entity mapping, `get_for_update` uses `with_for_update()`) in `src/aeropass/adapters/db/repositories.py` and expose it on `SqlAlchemyUnitOfWork` in `src/aeropass/adapters/db/unit_of_work.py`
- [X] T036 [P] [US1] Implement `InMemoryPassengerRepository` and `InMemoryUnitOfWork` (enforcing the same uniqueness constraints; for service unit tests only) in `src/aeropass/adapters/fakes/repositories.py`
- [X] T037 [US1] Implement `RegistrationService.register(user, datos, foto: ImageInput) -> (Pasajero, created: bool)` and `get_mine(user)` in `src/aeropass/services/registration_service.py` following plan.md "Registration": validate data and photo first; same account + same document → existing (created=False, no upload); document on another account → `DocumentoYaRegistrado`; account already has a different document → `CuentaYaRegistrada`; upload to `documentos/{pasajero_id}/rostro` outside any transaction (`MediaUnavailable` → `AlmacenamientoNoDisponible(retry_after=5)`); then short transaction to insert; DB unique violations mapped to the same domain errors (race safety)
- [X] T038 [US1] Add `PasajeroResponse` (masked number, `identidad_id` nullable, always `null` until US3) to `src/aeropass/api/schemas.py`
- [X] T039 [US1] Implement router `src/aeropass/api/routers/identity.py` (`POST /v1/identity` multipart with form fields + `foto_documento: UploadFile`, rejecting by `Content-Length` > 4.5 MB before reading, → 201/200; `GET /v1/identity/me` → 200/404), add `get_registration_service` to `src/aeropass/api/deps.py` and include the router in `src/aeropass/main.py`

**Checkpoint**: User Story 1 fully functional and testable independently (MVP)

---

## Phase 4: User Story 2 - Biometric verification with a selfie (Priority: P1)

**Goal**: the selfie is stored in the private store; the provider evaluates liveness + comparison against the document photo behind a circuit breaker; the attempt is persisted with both scores and the image reference; the passenger changes status. No transaction stays open during external calls.

**Independent Test**: with a `PENDIENTE_VERIFICACION` passenger (via US1 or a fixture), `POST /v1/biometrics/verifications` with the `ok` image → `EXITOSO` and passenger `VERIFICADO`; `spoof` → `FALLIDO/LIVENESS`; three failures → `REQUIERE_REVISION_MANUAL`; provider down → `NO_CONCLUYENTE` without counting an attempt; storage failure → 503 with no attempt.

### Tests for User Story 2 ⚠️

- [X] T040 [P] [US2] Unit tests in `tests/unit/test_verification_rules.py`: `EXITOSO` iff both scores ≥ thresholds (inclusive), `LIVENESS` has priority over `COMPARACION`, `NO_CONCLUYENTE` has null scores; passenger transitions (FALLIDO increments counter, 3rd FALLIDO → `REQUIERE_REVISION_MANUAL`, NO_CONCLUYENTE does not count, EXITOSO → `VERIFICADO`, VERIFICADO/REQUIERE_REVISION_MANUAL reject new attempts)
- [X] T041 [P] [US2] Contract tests in `tests/contract/test_biometrics_contract.py` for `POST /v1/biometrics/verifications` responses 200 (each resultado), 404, 409, 413, 415, 503
- [X] T042 [P] [US2] Integration tests in `tests/integration/test_biometric_verification.py` for spec US2 scenarios 1–5 and edge cases (unsupported format, > 4 MB, already verified, reference photo download failure → 503 without attempt); assert the provider received the registered document photo as reference, and the DB row stores only the selfie pathname/URL; plus a concurrency test: two simultaneous selfies for the same passenger → only one attempt counts toward the final state, the other is rejected with `ESTADO_NO_PERMITE_VERIFICACION` when the first was EXITOSO
- [X] T043 [P] [US2] Integration test in `tests/integration/test_biometric_circuit_breaker.py`: with the `timeout` image, 5 requests return `NO_CONCLUYENTE`, the 6th returns `NO_CONCLUYENTE` in < 1 s without calling the provider, `intentos_fallidos` unchanged; each request completes in < 10 s (SC-007)

### Implementation for User Story 2

- [X] T044 [P] [US2] Implement `BiometricResult` (score_liveness, score_comparacion, proveedor), `Thresholds`, and pure function `evaluate_outcome(result | None, thresholds) -> (ResultadoIntento, MotivoFallo | None)` in `src/aeropass/domain/verification.py`
- [X] T045 [US2] Add `Pasajero.assert_can_verify()` and `Pasajero.apply_outcome(resultado)` (rules in T040; max 3 failed attempts as a constant) to `src/aeropass/domain/passenger.py`
- [X] T046 [P] [US2] Define `BiometricProvider` protocol (`async evaluate(selfie: ImageInput, referencia: ImageInput) -> BiometricResult`) and `ProviderUnavailable` error in `src/aeropass/ports/biometric_provider.py`
- [X] T047 [P] [US2] Implement `MockBiometricAdapter` in `src/aeropass/adapters/biometrics/mock_adapter.py`: reads the `MOCK:<marker>` from the selfie bytes (no marker → `ok`): `ok` → 0.95/0.93, `spoof` → liveness 0.20 / match 0.90, `other` → liveness 0.95 / match 0.30, `timeout` → sleeps past the timeout (so the breaker's `wait_for` fires); ignores the reference content but asserts it is non-empty
- [X] T048 [P] [US2] Implement `VisionProviderAdapter` (generic HTTP POST multipart with selfie + reference to `VISION_PROVIDER_URL` with API key, translates provider JSON into `BiometricResult`, raises `ProviderUnavailable` on 5xx/malformed body) in `src/aeropass/adapters/biometrics/vision_adapter.py`
- [X] T049 [US2] Implement `ResilientBiometricProvider` (wraps any `BiometricProvider` with `CircuitBreaker` named `biometric` and `BIOMETRIC_TIMEOUT_SECONDS=4`; returns `None` on `ProviderUnavailable`/`CircuitOpenError`/timeout) and `BiometricProviderFactory.create(settings, breaker_store)` selecting mock/vision in `src/aeropass/adapters/biometrics/factory.py` (depends on T010, T047, T048)
- [X] T050 [P] [US2] Add `IntentoVerificacionRow` ORM mapping (table `intentos_verificacion`, columns and CHECKs per data-model.md: motivo only when FALLIDO, scores in 0–1) in `src/aeropass/adapters/db/orm.py`
- [X] T051 [US2] Create migration `migrations/versions/0002_intentos_verificacion.py` (depends on T050)
- [X] T052 [US2] Add `VerificationAttemptRepository` protocol (`add`, `count_failed(pasajero_id)`) and `UnitOfWork.attempts` in `src/aeropass/ports/repositories.py`; implement SQL version in `src/aeropass/adapters/db/repositories.py` and in-memory version in `src/aeropass/adapters/fakes/repositories.py`
- [X] T053 [US2] Implement `BiometricVerificationService.record_attempt(uow, pasajero, stored_selfie, result, thresholds) -> IntentoVerificacion` (applies `evaluate_outcome` + `Pasajero.apply_outcome`, no commit) in `src/aeropass/services/biometric_verification_service.py`
- [X] T054 [US2] Implement `IdentityVerificationFacade.verify_and_create_identity(pasajero: Pasajero, doc: DocumentoRegistrado, selfie: ImageInput) -> VerificationOutcome` (constitution signature) in `src/aeropass/services/identity_verification_facade.py` following plan.md "Selfie verification" steps 1–6 (identity creation added in US3): `validate_image`; `pasajero.assert_can_verify()` without lock; `asyncio.gather` of selfie upload to `selfies/{pasajero_id}/{intento_id}` and `MediaStorage.get(doc.foto.pathname)` (`MediaUnavailable` → `AlmacenamientoNoDisponible`); evaluate via resilient provider; then a short transaction that reloads the passenger `FOR UPDATE`, re-checks `assert_can_verify()`, records the attempt and commits
- [X] T055 [US2] Add `ResultadoVerificacionResponse` (with `intentos_restantes`, `reintentar_en_segundos = 30` when NO_CONCLUYENTE, `identidad_id` null) to `src/aeropass/api/schemas.py`
- [X] T056 [US2] Implement router `src/aeropass/api/routers/biometrics.py` (`POST /v1/biometrics/verifications`, multipart field `selfie`, rejects by `Content-Length` > 4.5 MB before reading; resolves `pasajero` and `doc` for the authenticated user via `RegistrationService.get_mine` → `PasajeroNoRegistrado` if absent), wire provider factory and facade in `src/aeropass/api/deps.py`, include router in `src/aeropass/main.py`

**Checkpoint**: User Stories 1 AND 2 work independently

---

## Phase 5: User Story 3 - Digital identity creation (Priority: P2)

**Goal**: after an `EXITOSO` attempt an `ACTIVA` IdentidadDigital is created in the same transaction and `credencial.emitida` is published via outbox + QStash without blocking the passenger or losing events (delivery < 5 min after recovery).

**Independent Test**: trigger a successful verification → exactly one `ACTIVA` identity exists and one event in `outbox_eventos` reaches the QStash fake, validating against `contracts/events/credencial.emitida.v1.json`; with QStash down the identity exists, the event stays `PENDIENTE` and `POST /internal/outbox/dispatch` delivers it on recovery.

### Tests for User Story 3 ⚠️

- [X] T057 [P] [US3] Contract tests in `tests/contract/test_event_contract.py`: payload produced by `CredencialEmitidaV1` validates against `contracts/events/credencial.emitida.v1.json` and contains no document data or image references; the `examples` of both `credencial.emitida.v1.json` and `validacion.fallida.v1.json` validate against their own schemas
- [X] T058 [P] [US3] Integration tests in `tests/integration/test_identity_creation.py` for spec US3 scenarios 1 and 3 (identity + outbox row committed in the same transaction as the attempt; second success does not create a second ACTIVE identity — partial unique index enforced); `GET /v1/identity/me` returns `identidad_id`
- [X] T059 [P] [US3] Integration test in `tests/integration/test_outbox_recovery.py` (SC-006): fake publisher down → response still 200 with `identidad_id`, event `PENDIENTE` with `intentos=1`; simulate a 30-minute outage with `FakeClock` and repeated dispatch calls (backoff grows to the 180 s cap); publisher back up → with dispatch every 60 simulated seconds the event reaches `ENTREGADO` within 240 simulated seconds; publisher received `deduplication_id == evento.id`; invalid signature → 401

### Implementation for User Story 3

- [X] T060 [P] [US3] Implement `IdentidadDigital` entity (`crear(pasajero_id, intento_id)`, `revocar()`) in `src/aeropass/domain/identity.py`
- [X] T061 [P] [US3] Implement `DomainEvent` base (id UUIDv7, tipo, version, ocurrido_at, datos, `to_payload()`) and `CredencialEmitidaV1.from_identity(identidad)` (with `tipo_credencial = "IDENTIDAD_DIGITAL"`) in `src/aeropass/domain/events.py`
- [X] T062 [P] [US3] Define `EventPublisher` protocol (`async publish(event_id, tipo, payload) -> None`, raises `PublishFailed`) in `src/aeropass/ports/event_publisher.py`
- [X] T063 [P] [US3] Add `IdentidadDigitalRow` and `OutboxEventoRow` ORM mappings (partial unique index `(pasajero_id) WHERE estado='ACTIVA'`, `UNIQUE(intento_origen_id)`, outbox partial index on `proximo_intento_at WHERE estado='PENDIENTE'`) in `src/aeropass/adapters/db/orm.py`
- [X] T064 [US3] Create migration `migrations/versions/0003_identidades_outbox.py` (depends on T063)
- [X] T065 [US3] Add `IdentityRepository` (`add`, `get_active(pasajero_id)`) and `OutboxRepository` (`add(event)`, `get(ids)`, `due_for_dispatch(now, limit)` with `FOR UPDATE SKIP LOCKED`, `mark_delivered(id, at)`, `mark_failed(id, error, next_at)`) protocols plus `UnitOfWork.identities` / `UnitOfWork.outbox` in `src/aeropass/ports/repositories.py`; implement SQL versions in `src/aeropass/adapters/db/repositories.py` and in-memory versions in `src/aeropass/adapters/fakes/repositories.py`
- [X] T066 [P] [US3] Implement Singleton `AsyncQStash` client in `src/aeropass/adapters/qstash/client.py` and `QStashEventPublisher` (`publish_json` to URL group `QSTASH_EVENTS_URL_GROUP`, header `Upstash-Deduplication-Id = event_id`, `QSTASH_TIMEOUT_SECONDS=2`, wrapped in `CircuitBreaker` named `qstash`) in `src/aeropass/adapters/qstash/publisher.py`
- [X] T067 [P] [US3] Implement `InMemoryEventPublisher` (records published messages, `down` switch) in `src/aeropass/adapters/fakes/event_publisher.py`
- [X] T068 [P] [US3] Implement QStash signature verification dependency `verify_qstash_signature` (uses `qstash.Receiver` with current/next signing keys; raises 401) in `src/aeropass/adapters/qstash/receiver.py` and a fake that accepts `Upstash-Signature: test` in `src/aeropass/adapters/fakes/qstash_receiver.py`
- [X] T069 [US3] Implement `IdentityService.create_for_success(uow, pasajero, intento) -> (IdentidadDigital, DomainEvent | None)` (idempotent: returns the existing ACTIVE identity and no new event) that adds identity and outbox row in the caller's transaction in `src/aeropass/services/identity_service.py`
- [X] T070 [US3] Implement `OutboxDispatcher` in `src/aeropass/services/outbox_dispatcher.py`: `publish_now(event_ids)` (best effort after commit, never raises; each attempt runs in its own short transaction) and `dispatch_due(limit=100)` with exponential backoff `min(2^intentos, 180)` seconds (data-model.md, research R7); returns counts `{entregados, pendientes}`
- [X] T071 [US3] Extend `IdentityVerificationFacade` in `src/aeropass/services/identity_verification_facade.py`: when the outcome is `EXITOSO` call `IdentityService.create_for_success` inside the same short transaction before commit, then call `OutboxDispatcher.publish_now` after commit; include `identidad_id` in `VerificationOutcome`
- [X] T072 [US3] Implement router `src/aeropass/api/routers/internal.py` (`POST /internal/outbox/dispatch`, protected by `verify_qstash_signature`, idempotent, returns `{eventos_entregados, eventos_pendientes, credenciales_expiradas: 0}`); wire publisher, receiver, identity service and dispatcher in `src/aeropass/api/deps.py`; include router in `src/aeropass/main.py`; populate `identidad_id` in `ResultadoVerificacionResponse` and `PasajeroResponse` in `src/aeropass/api/schemas.py`

**Checkpoint**: User Stories 1–3 work; `credencial.emitida` is never lost

---

## Phase 6: User Story 4 - Dynamic QR generation (CredencialAcceso) (Priority: P2)

**Goal**: a passenger with an active identity gets a signed (Ed25519) 30–60 s CredencialAcceso, built with a Builder, with a single-use token in Redis, immutable history (including rejections), a state machine (State) with RN-06, automatic renewal and a limit of 30 issuances/min.

**Independent Test**: with an `ACTIVA` identity (via US3 or a fixture), `POST /v1/passes {"codigo_vuelo":"AV9380"}` → 201, token verifiable with `/.well-known/jwks.json`, `qr:{jti}` = `ACTIVA` with the correct TTL, history `EMITIDA → ACTIVA`; a second issuance revokes the first; expired → `EXPIRADA`; 31st issuance within a minute → 429; double consumption → rejected (RN-06) and recorded.

### Tests for User Story 4 ⚠️

- [X] T073 [P] [US4] Unit tests in `tests/unit/test_credential_states.py` covering every cell of the transition table in data-model.md: accepted transitions return `TransitionResult(aceptada=True)`, rejected ones (including `CONSUMIDA → CONSUMIDA`, RN-06) return `aceptada=False` **without raising**, and every call appends a transition entry with the right `aceptada` flag
- [X] T074 [P] [US4] Unit tests in `tests/unit/test_credential_builder.py`: `build()` fails when passenger, identity, flight, permissions, TTL or signer is missing, TTL outside 30–60, or flight code does not match `^[A-Z0-9]{2}[0-9]{1,4}[A-Z]?$`; default permissions `["embarque"]`; built credential is `EMITIDA` with `expira_at = emitida_at + ttl`
- [X] T075 [P] [US4] Unit tests in `tests/unit/test_signing.py`: signed token verifies with the JWKS public key, carries claims `jti, sub, flt, perms, iat, exp` and header `kid`, fails verification when tampered or expired
- [X] T076 [P] [US4] Contract tests in `tests/contract/test_passes_contract.py` for `POST /v1/passes` (201, 403, 422, 429), `GET /v1/passes/{id}` (200, 404) and `GET /.well-known/jwks.json` (200)
- [X] T077 [P] [US4] Integration tests in `tests/integration/test_pass_issuance.py` for spec US4 scenarios 1–6 and edge case "document expires between registration and pass" → 403 `DOCUMENTO_VENCIDO` (uses `FakeClock` to advance time for expiry); on renewal assert the previous `qr:{jti}` is deleted only after the new credential is committed
- [X] T078 [P] [US4] Integration test in `tests/integration/test_concurrent_passes.py`: 2 simultaneous `POST /v1/passes` for the same passenger and flight → exactly one `ACTIVA`, the other `REVOCADA`; partial unique index never violated in the final state
- [X] T079 [P] [US4] Integration test in `tests/integration/test_concurrent_consume.py` (SC-004): 50 concurrent `CredentialLifecycleService.consume(jti)` calls → exactly one `CONSUMIDA`, 49 `YA_CONSUMIDA`, 50 transition rows persisted (1 accepted, 49 rejected)
- [X] T080 [P] [US4] Integration test in `tests/integration/test_transitions_immutable.py`: raw SQL `UPDATE` and `DELETE` on `transiciones_credencial` raise an error
- [X] T081 [P] [US4] Integration test in `tests/integration/test_rejected_transition_persisted.py` (FR-019): consuming an `EXPIRADA` credential and revoking a `CONSUMIDA` credential return rejected results and each leaves a committed row with `aceptada=false` and motivo `TRANSICION_INVALIDA`

### Implementation for User Story 4

- [X] T082 [P] [US4] Implement State pattern in `src/aeropass/domain/credential/states.py`: abstract `CredentialState` with `allowed: frozenset[EstadoCredencial]` and `can_transition_to(target) -> bool`; one class per state (`Emitida`, `Activa`, `Consumida`, `Expirada`, `Revocada`) encoding the table in data-model.md; `state_for(EstadoCredencial)` lookup
- [X] T083 [US4] Implement `CredencialAcceso` aggregate in `src/aeropass/domain/credential/credential.py`: fields per data-model.md, methods `activar()`, `consumir(actor)`, `expirar()`, `revocar(motivo)` each returning `TransitionResult(aceptada, estado_actual)` (never raising for invalid transitions), delegating the decision to the State object and appending a `Transicion(estado_anterior, estado_solicitado, aceptada, motivo, actor)` to `pending_transitions` (motivo `TRANSICION_INVALIDA` when rejected); `is_expired(now)` (depends on T082)
- [X] T084 [P] [US4] Implement `CredentialSigner` (Ed25519 private key PEM from settings, `sign(claims) -> token`, `signature_of(token)`) and `CredentialVerifier` (`verify(token) -> claims`, `jwks() -> dict`) with PyJWT `EdDSA` in `src/aeropass/domain/credential/signing.py`
- [X] T085 [US4] Implement `CredencialAccesoBuilder` in `src/aeropass/domain/credential/builder.py` (`para_pasajero`, `con_identidad`, `para_vuelo`, `con_permisos`, `con_ttl`, `firmado_con`, `emitido_en`, `build() -> (CredencialAcceso, token)`) enforcing the rules tested in T074; generates `id` (UUIDv7) as `jti` (depends on T083, T084)
- [X] T086 [P] [US4] Implement key generator CLI `python -m aeropass.tools.gen_signing_key` printing `QR_SIGNING_PRIVATE_KEY` (PEM) and `QR_SIGNING_KID` in `src/aeropass/tools/gen_signing_key.py`
- [X] T087 [P] [US4] Define `TokenStore` protocol (`register(jti, ttl)`, `revoke(jti)`, `consume(jti) -> bool`, `is_active(jti) -> bool`) in `src/aeropass/ports/token_store.py`, `RateLimiter` protocol (`hit(key) -> RateLimitResult(allowed, retry_after)`) in `src/aeropass/ports/rate_limiter.py`, and `FlightCatalog` protocol (`validate(codigo_vuelo)`) in `src/aeropass/ports/flight_catalog.py`
- [X] T088 [P] [US4] Implement `UpstashTokenStore` in `src/aeropass/adapters/redis/token_store.py` (`SET qr:{jti} ACTIVA EX ttl NX`, `DEL`, Lua script for atomic `ACTIVA → CONSUMIDA` with `KEEPTTL`, research R6) and `RedisVerificationProxy` in `src/aeropass/adapters/redis/token_proxy.py` (answers `is_active` from Redis before any Postgres read; a missing key means not usable)
- [X] T089 [P] [US4] Implement `UpstashRateLimiter` (sliding window 30 / 60 s, key `rl:passes:{pasajero_id}`) in `src/aeropass/adapters/redis/rate_limiter.py`
- [X] T090 [P] [US4] Implement `FormatOnlyFlightCatalog` in `src/aeropass/adapters/flights/format_only_catalog.py` (regex only; extension point for `AirlineApiAdapter`)
- [X] T091 [P] [US4] Implement in-memory `FakeTokenStore` (TTL via `Clock`, atomic consume with `asyncio.Lock`, `fail_next_register` switch) and `FakeRateLimiter` in `src/aeropass/adapters/fakes/token_store.py` and `src/aeropass/adapters/fakes/rate_limiter.py`
- [X] T092 [P] [US4] Add `CredencialAccesoRow` and `TransicionCredencialRow` ORM mappings (partial unique index `(pasajero_id, codigo_vuelo) WHERE estado IN ('EMITIDA','ACTIVA')`, index `(estado, expira_at)`, CHECK `expira_at - emitida_at BETWEEN interval '30 seconds' AND interval '60 seconds'`, `permisos text[]` non-empty) in `src/aeropass/adapters/db/orm.py`
- [X] T093 [US4] Create migration `migrations/versions/0004_credenciales.py` with both tables, indexes, CHECKs and a `BEFORE UPDATE OR DELETE` trigger on `transiciones_credencial` raising an exception (depends on T092)
- [X] T094 [US4] Add `CredentialRepository` protocol (`add`, `get(id)`, `get_for_owner(id, pasajero_id)`, `get_live_for_update(pasajero_id, codigo_vuelo)`, `save(c)` persisting `pending_transitions`, `conditional_consume(id) -> bool` using `UPDATE … WHERE estado='ACTIVA'`, `expired_live(now, limit)`) and `UnitOfWork.credentials` in `src/aeropass/ports/repositories.py`; implement SQL version in `src/aeropass/adapters/db/repositories.py` and in-memory version in `src/aeropass/adapters/fakes/repositories.py`
- [X] T095 [US4] Implement `CredentialLifecycleService` in `src/aeropass/services/credential_lifecycle_service.py`: `refresh_expiry(uow, c)` (lazy expiry), `revoke(uow, c, motivo) -> TransitionResult` (Redis `TokenStore.revoke` is returned as a post-commit action, never called inside the transaction), `consume(jti, actor) -> ConsumeResult` (`CONSUMIDA | YA_CONSUMIDA | EXPIRADA | REVOCADA | NO_ENCONTRADA`; DB conditional update + `TokenStore.consume`; **always commits** so rejected transitions persist; docstring maps each result to the `motivo` enum of `contracts/events/validacion.fallida.v1.json`), `sweep_expired(limit=500) -> int`
- [X] T096 [US4] Implement `PassIssuanceService.issue(user, codigo_vuelo) -> IssuedPass` in `src/aeropass/services/pass_issuance_service.py` following plan.md "Pass issuance" steps 1–8: rate limit → `LimiteEmisionExcedido(retry_after)`; lock passenger; require ACTIVE identity (`IdentidadNoActiva`) and valid document (`DocumentoVencido`); `FlightCatalog.validate`; revoke live credential for same flight in Postgres (motivo `RENOVACION`); build with `CredencialAccesoBuilder`; persist `EMITIDA`; `TokenStore.register`; `activar()`; commit; **after commit** `TokenStore.revoke(previous_jti)` (errors logged, not raised); on token-store register failure mark `REVOCADA`, commit and raise `AlmacenamientoNoDisponible`; return token and `renovar_en_segundos = ttl - 5`
- [X] T097 [US4] Add `EmitirPaseRequest`, `PaseResponse`, `DetallePaseResponse` to `src/aeropass/api/schemas.py`
- [X] T098 [US4] Implement router `src/aeropass/api/routers/passes.py` (`POST /v1/passes` → 201, `GET /v1/passes/{credencial_id}` applying lazy expiry, 404 when not owned) and `src/aeropass/api/routers/wellknown.py` (`GET /.well-known/jwks.json`, public, `Cache-Control: public, max-age=300`); wire signer, token store, proxy, rate limiter, flight catalog and services in `src/aeropass/api/deps.py`; include routers in `src/aeropass/main.py`
- [X] T099 [US4] Extend `POST /internal/outbox/dispatch` in `src/aeropass/api/routers/internal.py` to call `CredentialLifecycleService.sweep_expired()` and report `credenciales_expiradas`

**Checkpoint**: all user stories independently functional

---

## Phase 7: Polish & Cross-Cutting Concerns

**Purpose**: Improvements that affect multiple user stories

- [X] T100 [P] Add script `src/aeropass/tools/setup_qstash_schedule.py` that creates (idempotently) the QStash schedule calling `POST <BASE_URL>/internal/outbox/dispatch` every minute and the URL group `aeropass-eventos`
- [X] T101 [P] Add benchmark CLI `python -m aeropass.tools.bench_passes --base-url --tokens <file> --n 200` in `src/aeropass/tools/bench_passes.py` (SC-002): distributes `POST /v1/passes` round-robin across the tokens (≤ 25/min per account), sequential per account, prints p50/p95/p99 latency and exits non-zero when p95 ≥ 1 s
- [X] T102 [P] Write `README.md` at repo root: architecture summary (link to plan.md), local setup, adapter modes, running tests, deploy to Vercel, QStash schedule setup, signing key rotation (`kid`), benchmark
- [X] T103 [P] Add PII guard unit test in `tests/unit/test_no_pii_in_logs.py`: run registration and verification with `caplog` and assert neither the full document number nor image bytes (selfie or document photo) appear in logs
- [X] T104 Run `uv run ruff check`, `uv run ruff format --check` and `uv run mypy src` and fix all findings across `src/aeropass/`
- [X] T105 Verify constitution compliance: no module outside `src/aeropass/api/deps.py` and `src/aeropass/adapters/` imports concrete adapters; every biometric-provider and QStash call goes through `CircuitBreaker`; the Facade signature is `verify_and_create_identity(pasajero, doc, selfie)`; both event schemas exist in `contracts/events/`; no transaction spans an external call; fix violations
- [ ] T106 Run the full `specs/001-persistencia-registro-qr/quickstart.md` validation (automated tests, manual walkthrough steps 1–8 with `AEROPASS_ADAPTERS=fake`, and the "Validation on Vercel" section including `bench_passes` on a preview deployment) and record results in `specs/001-persistencia-registro-qr/checklists/quickstart-run.md`

---

## Dependencies & Execution Order

### Phase Dependencies

- **Setup (Phase 1)**: no dependencies
- **Foundational (Phase 2)**: depends on Setup — BLOCKS all user stories
- **US1 (Phase 3)**: depends on Foundational
- **US2 (Phase 4)**: depends on US1 (needs `Pasajero`, `DocumentoRegistrado` with the photo, `pasajeros` table, `PassengerRepository`)
- **US3 (Phase 5)**: depends on US2 (triggered by an `EXITOSO` attempt; extends the facade)
- **US4 (Phase 6)**: depends on US3 data (`identidades_digitales` table, `IdentityRepository`); its domain tasks (T073–T075, T082–T086) can start right after Foundational
- **Polish (Phase 7)**: depends on all stories

### Story Dependency Graph

```text
Setup → Foundational → US1 → US2 → US3 → US4 → Polish
             │                              ▲
             └── US4 domain (states, builder, signing) in parallel
```

### Within Each User Story

- Tests first (must fail), then domain → ports → ORM/migration → adapters/fakes → services → schemas/router
- Migrations are sequential (`0000` → `0004`) because each depends on earlier tables
- Tasks editing the same shared file (`orm.py`, `repositories.py`, `deps.py`, `schemas.py`, `main.py`, `passenger.py`, `identity_verification_facade.py`) are never marked [P] relative to each other

### Parallel Opportunities

- Setup: T003, T004
- Foundational: T006–T011 together; T017–T022 together; then T026, T027
- US1: T028–T030 together; T031, T032 together; T035, T036 together
- US2: T040–T043 together; T044, T046–T048, T050 together
- US3: T057–T059 together; T060–T063, T066–T068 together
- US4: T073–T081 together; T082, T084, T086–T092 together
- Polish: T100–T103

---

## Parallel Example: User Story 4

```bash
# Tests first:
Task: "Unit tests for state table in tests/unit/test_credential_states.py"
Task: "Unit tests for builder in tests/unit/test_credential_builder.py"
Task: "Unit tests for signing in tests/unit/test_signing.py"
Task: "Concurrent consume test in tests/integration/test_concurrent_consume.py"
Task: "Rejected transition persisted test in tests/integration/test_rejected_transition_persisted.py"

# Then independent building blocks:
Task: "State pattern in src/aeropass/domain/credential/states.py"
Task: "Signer/Verifier in src/aeropass/domain/credential/signing.py"
Task: "UpstashTokenStore + RedisVerificationProxy in src/aeropass/adapters/redis/"
Task: "UpstashRateLimiter in src/aeropass/adapters/redis/rate_limiter.py"
Task: "FormatOnlyFlightCatalog in src/aeropass/adapters/flights/format_only_catalog.py"
```

---

## Implementation Strategy

### MVP First (User Story 1 Only)

1. Phase 1 Setup → Phase 2 Foundational
2. Phase 3 US1 → **STOP and VALIDATE** with T028–T030 and quickstart step 1
3. Deploy a Vercel preview

### Incremental Delivery

1. Setup + Foundational → foundation ready
2. + US1 → registration with the document photo (MVP)
3. + US2 → biometric verification with liveness and circuit breaker
4. + US3 → digital identity and `credencial.emitida` event (first integration point with other teams)
5. + US4 → dynamic QR; unblocks the checkpoint team (`consume()`, JWKS, `validacion.fallida` v1)

### Parallel Team Strategy

- Developer A: US1 → US2 → US3 (passenger flow)
- Developer B (after Foundational): US4 domain (T073–T075, T082–T086) and Redis adapters (T087–T091); joins US4 services once US3 migrations land

---

## Notes

- [P] tasks = different files, no dependencies on incomplete tasks
- Out of scope (do NOT implement): `validate` endpoint, agent console, `flights` endpoint, full observability, image retention job (retention periods pending legal confirmation), publishing `validacion.fallida` (only its schema lives here)
- Commit after each task or logical group; stop at any checkpoint to validate the story

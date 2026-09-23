# Implementation Plan: Persistencia del flujo de registro, verificación biométrica y QR

**Branch**: `001-persistencia-registro-qr` | **Date**: 2026-09-23 | **Spec**: [spec.md](spec.md)

**Input**: Feature specification from `/specs/001-persistencia-registro-qr/spec.md`

## Summary

Backend FastAPI desplegado en Vercel que persiste las cuatro etapas del alta del pasajero:
(1) datos del documento en Neon Postgres y la foto del rostro del documento en un store
**privado** de Vercel Blob; (2) selfie en el mismo store privado, evaluada en servidor por un
proveedor biométrico (prueba de vida + comparación contra la foto del documento) detrás de un
circuit breaker, con el resultado y las referencias a los blobs en Postgres; (3) IdentidadDigital y
evento `credencial.emitida` publicado vía QStash con **transactional outbox**, para no perderlo ni
bloquear al pasajero; (4) CredencialAcceso firmada con Ed25519 (JWS), construida con un Builder,
con token de uso único en Upstash Redis, historial inmutable en Postgres, máquina de estados
(patrón State) que aplica RN-06, renovación automática y límite de 30 emisiones/min.

## Technical Context

**Language/Version**: Python 3.11+

**Primary Dependencies**: FastAPI, Pydantic v2 + pydantic-settings, SQLAlchemy 2.x (async) +
asyncpg, Alembic, `upstash-redis`, `upstash-ratelimit`, `qstash`, `clerk-backend-api`,
`PyJWT[crypto]` (EdDSA), `httpx` (Blob y proveedor biométrico), `python-multipart`

**Storage**: Neon Postgres (relacional, fuente de verdad), Vercel Blob privado (selfies y foto
del documento),
Upstash Redis (token de uso único, rate limit, estado del circuit breaker)

**Testing**: pytest, pytest-asyncio, httpx `AsyncClient` (ASGI), jsonschema; Postgres real
(docker o rama de Neon) para integración; dobles en memoria de todos los puertos

**Target Platform**: Vercel Functions, runtime Python (serverless, arranque en frío esperado)

**Project Type**: web-service (API REST solo backend)

**Performance Goals**: emisión de pase p95 < 1 s con instancia caliente (SC-002); respuesta con
proveedor biométrico caído < 10 s p99 (SC-007); renovación del QR antes de su vencimiento en el
99 % de los casos (SC-008)

**Constraints**: cuerpo de petición ≤ 4,5 MB (cada imagen ≤ 4 MB); sin estado de negocio en
memoria entre peticiones; timeouts: proveedor biométrico 4 s, Blob 3 s (subida de la selfie y
descarga de la foto de referencia en paralelo), QStash 2 s → peor caso de la verificación ~7 s
(SC-007 < 10 s); ninguna transacción de Postgres abierta durante llamadas externas; región de la
función co-ubicada con Neon, Blob y Upstash

**Scale/Scope**: *supuesto a validar con el equipo*: piloto de un aeropuerto; ~5.000
pasajeros/día, picos de ~200 pases abiertos a la vez → ~7 emisiones/s por renovación automática;
~20 credenciales por pasajero por sesión de fila

## Constitution Check

*GATE: Must pass before Phase 0 research. Re-check after Phase 1 design.*

| # | Principio / regla | Cumplimiento en el diseño | Estado |
|---|---|---|---|
| I | Stack canónico | FastAPI, Neon, Vercel Blob, Upstash Redis/QStash, Clerk, Vercel | ✅ |
| I | Sin API Gateway: cada endpoint valida | Dependencia `get_current_user` (Clerk) por router; `/internal/*` valida firma QStash | ✅ |
| I | QStash es HTTP con reintentos | Consumidores deduplican por `id`; outbox propio para lo que QStash no cubre; `/internal/outbox/dispatch` es idempotente (`FOR UPDATE SKIP LOCKED`) | ✅ |
| I | Sin estado en memoria | Estado del breaker en Redis; outbox en Postgres; solo cachés (JWKS) en proceso | ✅ |
| II | Solo registro, biometría, identidad, QR | `validate`, consola del agente, `flights` y observabilidad no se implementan | ✅ |
| II | Puntos de extensión listos | `consume()` RN-06, JWKS, esquema de eventos, `FlightCatalog` — ver [extension-points.md](contracts/extension-points.md) | ✅ |
| III | SOLID / DI por abstracción | Puertos en `ports/`; servicios reciben puertos por constructor; wiring solo en `api/deps.py` | ✅ |
| IV | DRY | Reglas de resultado biométrico, transiciones, firma y TTL viven una sola vez en `domain/` | ✅ |
| V | Circuit breaker en externos inestables | Proveedor biométrico y QStash envueltos; fallback `NO_CONCLUYENTE` / evento pendiente | ✅ |
| VI | Hooks de observabilidad | Decorador `@traced` (no-op por defecto) en servicios y adapters; sin dependencia de Sentry/OTel | ✅ |
| Patrones | Factory, Builder, Singleton, Adapter, Facade, Proxy, Decorator, Observer, State | Ver tabla "Patrones → ubicación" | ✅ |
| Patrones | Facade con interfaz `verify_and_create_identity(pasajero, doc, selfie)` | Firma idéntica a la constitución | ✅ |
| Patrones | Esquemas de `credencial.emitida` y `validacion.fallida` versionados en este repo | `contracts/events/*.v1.json` (el segundo lo produce el checkpoint) | ✅ |
| Patrones | Strategy, Chain of Responsibility | Solo documentados (checkpoint, fuera de alcance) | ✅ documentado |

**Resultado**: pasa sin violaciones. Re-evaluado tras la Fase 1 y tras `/speckit-analyze`
(se corrigieron la firma del Facade y el esquema faltante de `validacion.fallida`).

### Patrones → ubicación

| Patrón | Dónde |
|---|---|
| Factory | `adapters/biometrics/factory.py` (`BiometricProviderFactory`), `api/deps.py` (fakes vs reales) |
| Builder | `domain/credential/builder.py` (`CredencialAccesoBuilder`: pasajero → vuelo → permisos → ttl → firma → `build()` valida todo) |
| Singleton | `adapters/db/engine.py`, `adapters/redis/client.py`, `adapters/blob/client.py`, `adapters/qstash/client.py` (instancia por proceso, `functools.cache`) |
| Adapter | `adapters/biometrics/vision_adapter.py`, `adapters/blob/vercel_blob_storage.py` |
| Facade | `services/identity_verification_facade.py` — `verify_and_create_identity(pasajero, doc, selfie)`; `doc` = `DocumentoRegistrado` (datos + referencia a la foto), `selfie` = `ImageInput` |
| Proxy | `adapters/redis/token_proxy.py` (`RedisVerificationProxy`: consulta `qr:{jti}` antes de Postgres) |
| Decorator | `observability/hooks.py` (`@traced`, `@audited`) sobre servicios y sobre la dependencia de autenticación/autorización (`get_current_user`) |
| Observer | `ports/event_publisher.py` + outbox + URL group de QStash |
| State | `domain/credential/states.py` (una clase por estado con `transition_to()`) |
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
│       └── validacion.fallida.v1.json   # producido por el checkpoint; definido aquí
├── checklists/requirements.md
└── tasks.md
```

### Source Code (repository root)

```text
api/
└── index.py                         # entrypoint de Vercel: from aeropass.main import app

src/aeropass/
├── main.py                          # create_app(), routers, manejadores de error
├── config.py                        # Settings (pydantic-settings)
├── domain/                          # reglas de negocio puras, sin I/O
│   ├── errors.py
│   ├── passenger.py                 # Pasajero, EstadoPasajero, regla de 3 intentos
│   ├── verification.py              # BiometricResult, umbrales, regla EXITOSO/FALLIDO
│   ├── identity.py
│   ├── events.py                    # DomainEvent, CredencialEmitidaV1
│   └── credential/
│       ├── states.py                # State: Emitida, Activa, Consumida, Expirada, Revocada
│       ├── credential.py            # agregado CredencialAcceso
│       ├── builder.py               # CredencialAccesoBuilder
│       └── signing.py               # CredentialSigner / CredentialVerifier (Ed25519)
├── ports/                           # abstracciones (Protocol)
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
│   ├── credential_lifecycle_service.py   # expire/revoke/consume (extensión checkpoint)
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
│   └── fakes/         # dobles en memoria de cada servicio externo (pruebas y AEROPASS_ADAPTERS=fake)
├── observability/
│   └── hooks.py                     # @traced / @audited (no-op hasta que el otro equipo los conecte)
├── api/
│   ├── deps.py                      # composición de dependencias (único lugar que instancia concretos)
│   ├── schemas.py                   # modelos Pydantic de request/response
│   └── routers/                     # identity.py, biometrics.py, passes.py, wellknown.py, internal.py
└── tools/
    └── gen_signing_key.py

migrations/                          # Alembic (enums, tablas, índices parciales, trigger)
tests/
├── unit/
├── integration/
└── contract/

pyproject.toml
vercel.json
```

**Structure Decision**: un solo proyecto backend con arquitectura hexagonal ligera: `domain/`
(reglas puras), `ports/` (abstracciones), `services/` (casos de uso), `adapters/`
(implementaciones concretas) y `api/` (FastAPI). Solo `api/deps.py` conoce las clases concretas,
lo que cumple SOLID/DI.

**Modos de adaptadores** (`AEROPASS_ADAPTERS`):

- `real`: Clerk, Vercel Blob, Upstash Redis, QStash y el proveedor elegido por
  `BIOMETRIC_PROVIDER`.
- `fake`: reemplaza **solo los servicios externos** (Clerk, Blob, Redis, QStash) por los dobles de
  `adapters/fakes/`. Postgres **siempre** es real (local o rama de Neon), porque las restricciones
  SQL (índices parciales, CHECK, trigger) son parte de la lógica.
- `InMemoryUnitOfWork` se usa únicamente en pruebas unitarias de servicios, nunca en ejecución.

## Flujos clave

**Registro** (`RegistrationService`):

1. Validar datos y foto (tipo por cabecera y firma de bytes, ≤ 4 MB) → 2. Resolver idempotencia
(misma cuenta + mismo documento → 200 sin subir nada) → 3. Subir la foto a
`documentos/{pasajero_id}/…` (si falla: 503, nada persistido) → 4. Transacción: insertar pasajero
con la referencia → commit (si una restricción única falla por carrera, se traduce al error de
dominio y la foto subida queda huérfana e identificable).

**Verificación de selfie** — `IdentityVerificationFacade.verify_and_create_identity(pasajero, doc,
selfie)`. El router resuelve `pasajero` y `doc` desde la cuenta autenticada:

1. Validar tipo y tamaño de la selfie → 2. `pasajero.assert_can_verify()` **sin bloqueo** →
3. En paralelo (`asyncio.gather`, 3 s): subir la selfie a Blob privado y descargar la foto de
referencia de `doc` (si alguna falla: 503, sin intento) → 4. `BiometricProvider.evaluate(selfie,
referencia)` vía circuit breaker, 4 s (si falla o está abierto: `NO_CONCLUYENTE`) → 5. Regla de
dominio → 6. Transacción corta: bloquear pasajero (`FOR UPDATE`), **revalidar** su estado (si
cambió: `EstadoNoPermiteVerificacion`, la selfie queda huérfana), registrar intento + estado del
pasajero + (si EXITOSO) IdentidadDigital + fila de outbox → commit → 7. Publicación inmediata a
QStash (best effort, 2 s) → 8. Respuesta.

**Emisión de pase** (`PassIssuanceService`):

1. Rate limit (Redis) → 2. Transacción: bloquear pasajero; validar identidad activa y documento
vigente; revocar en Postgres la credencial viva del mismo vuelo (transición `RENOVACION`) →
3. `CredencialAccesoBuilder` → firma Ed25519 → 4. Insertar credencial `EMITIDA` + transición →
5. `SET qr:{jti} ACTIVA EX ttl NX` → 6. Transición `EMITIDA → ACTIVA` → commit →
7. **Después del commit**: `DEL qr:{jti_anterior}` (si falla, caduca solo en ≤ 60 s) →
8. Respuesta con `renovar_en_segundos = ttl − 5`. Si el paso 5 falla, la credencial queda
`REVOCADA`, se hace commit y se responde 503.

**Transiciones de la credencial**: los métodos del agregado (`activar`, `consumir`, `expirar`,
`revocar`) devuelven `TransitionResult(aceptada, estado_actual)` y **nunca lanzan** por una
transición inválida; así la fila del rechazo se confirma en la misma transacción (FR-019) y el
servicio traduce el resultado a la respuesta después del commit.

## Complexity Tracking

Sin violaciones de la constitución que justificar.

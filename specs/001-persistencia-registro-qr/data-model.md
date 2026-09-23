# Data Model: Persistence of the registration, biometric verification and QR flow

**Feature**: `001-persistencia-registro-qr` | **Date**: 2026-09-23

Three stores, each with one responsibility:

| Store | What it stores | What it does NOT store |
|---|---|---|
| Neon Postgres | Passengers, attempts, identities, credentials, history, outbox | Images, ephemeral QR state |
| Vercel Blob (private) | Selfie bytes | Business metadata |
| Upstash Redis | QR single-use token, issuance limit, circuit breaker state | Anything that cannot be rebuilt or expire |

All tables use `created_at` / `updated_at` `timestamptz` in UTC. Identifiers are `uuid` (v7
generated in the application, time-sortable) unless stated otherwise.

## Enumerations (Postgres `enum` types, single source in `domain/`)

| Enum | Values |
|---|---|
| `tipo_documento` | `CC`, `CE`, `PASAPORTE` |
| `estado_pasajero` | `PENDIENTE_VERIFICACION`, `VERIFICADO`, `REQUIERE_REVISION_MANUAL` |
| `resultado_intento` | `EXITOSO`, `FALLIDO`, `NO_CONCLUYENTE` |
| `motivo_fallo` | `LIVENESS`, `COMPARACION` |
| `estado_identidad` | `ACTIVA`, `REVOCADA` |
| `estado_credencial` | `EMITIDA`, `ACTIVA`, `CONSUMIDA`, `EXPIRADA`, `REVOCADA` |
| `estado_evento` | `PENDIENTE`, `ENTREGADO` |

## Neon Postgres

### `pasajeros`

| Field | Type | Rules |
|---|---|---|
| `id` | uuid PK | |
| `clerk_user_id` | text | NOT NULL, UNIQUE (one account ↔ one passenger) |
| `nombre_completo` | text | NOT NULL, 2–200 characters |
| `tipo_documento` | `tipo_documento` | NOT NULL |
| `numero_documento` | text | NOT NULL, 4–20 alphanumeric characters, normalized to uppercase without separators |
| `fecha_vencimiento_documento` | date | NOT NULL, must be ≥ today at registration (FR-003) |
| `foto_documento_blob_pathname` | text | NOT NULL; private reference to the document's face photo (FR-001a) |
| `foto_documento_blob_url` | text | NOT NULL; private URL, never the content |
| `estado` | `estado_pasajero` | NOT NULL, default `PENDIENTE_VERIFICACION` (FR-002) |
| `intentos_fallidos` | smallint | NOT NULL, default 0, CHECK 0–3 |
| `created_at`, `updated_at` | timestamptz | |

- `UNIQUE (tipo_documento, numero_documento)` (FR-004).

**Passenger transitions**:

```text
PENDIENTE_VERIFICACION ──EXITOSO attempt──────────────▶ VERIFICADO
PENDIENTE_VERIFICACION ──3rd FALLIDO attempt──────────▶ REQUIERE_REVISION_MANUAL
PENDIENTE_VERIFICACION ──FALLIDO (<3) / NO_CONCLUYENTE─▶ PENDIENTE_VERIFICACION
```

`VERIFICADO` and `REQUIERE_REVISION_MANUAL` do not accept new selfies in this scope. The exit from
`REQUIERE_REVISION_MANUAL` will be defined by the agent console module (out of scope).

### `intentos_verificacion`

| Field | Type | Rules |
|---|---|---|
| `id` | uuid PK | |
| `pasajero_id` | uuid FK → `pasajeros.id` | NOT NULL, index |
| `selfie_blob_pathname` | text | NULL only if the image was already deleted by retention |
| `selfie_blob_url` | text | Private blob URL, never the content (FR-005) |
| `resultado` | `resultado_intento` | NOT NULL |
| `motivo_fallo` | `motivo_fallo` | NULL except when `resultado = FALLIDO` (CHECK) |
| `score_liveness` | numeric(4,3) | NULL if `NO_CONCLUYENTE`; CHECK 0–1 |
| `score_comparacion` | numeric(4,3) | NULL if `NO_CONCLUYENTE`; CHECK 0–1 |
| `umbral_liveness` | numeric(4,3) | NOT NULL (value in force at evaluation time) |
| `umbral_comparacion` | numeric(4,3) | NOT NULL |
| `proveedor` | text | NOT NULL (e.g. `mock`, `vision`) |
| `imagen_eliminada_at` | timestamptz | NULL until retention deletes the blob |
| `created_at` | timestamptz | |

**Result rule** (lives only in `domain/verification.py`):
`EXITOSO` ⇔ `score_liveness ≥ umbral_liveness` **and** `score_comparacion ≥ umbral_comparacion`.
If liveness fails, `motivo_fallo = LIVENESS` (takes priority); otherwise `COMPARACION`.

### `identidades_digitales`

| Field | Type | Rules |
|---|---|---|
| `id` | uuid PK | |
| `pasajero_id` | uuid FK → `pasajeros.id` | NOT NULL |
| `intento_origen_id` | uuid FK → `intentos_verificacion.id` | NOT NULL, UNIQUE |
| `estado` | `estado_identidad` | NOT NULL, default `ACTIVA` |
| `revocada_at` | timestamptz | NULL |
| `created_at` | timestamptz | |

- Partial unique index `(pasajero_id) WHERE estado = 'ACTIVA'` (FR-011).

### `credenciales_acceso`

| Field | Type | Rules |
|---|---|---|
| `id` | uuid PK | Also the `jti` of the signed token |
| `pasajero_id` | uuid FK → `pasajeros.id` | NOT NULL |
| `identidad_id` | uuid FK → `identidades_digitales.id` | NOT NULL |
| `codigo_vuelo` | text | NOT NULL, pattern `^[A-Z0-9]{2}[0-9]{1,4}[A-Z]?$` (e.g. `AV9380`) |
| `permisos` | text[] | NOT NULL, non-empty; default `{embarque}` |
| `firma` | text | NOT NULL, Ed25519 signature (base64url) of the token |
| `kid` | text | NOT NULL, id of the signing key used |
| `emitida_at` | timestamptz | NOT NULL |
| `expira_at` | timestamptz | NOT NULL; CHECK `expira_at - emitida_at BETWEEN 30s AND 60s` (FR-016) |
| `estado` | `estado_credencial` | NOT NULL |
| `updated_at` | timestamptz | |

- Partial unique index `(pasajero_id, codigo_vuelo) WHERE estado IN ('EMITIDA','ACTIVA')`
  (FR-020: at most one live credential per passenger and flight).
- Index `(estado, expira_at)` for the expiration sweep.

**State machine** (State pattern in `domain/credential/states.py`; each state knows its valid
transitions, there are no scattered `if`s):

```text
          ┌────────────▶ REVOCADA ◀───────────┐
          │                                   │
      EMITIDA ───────────▶ ACTIVA ──────────▶ CONSUMIDA
          │                  │
          └──▶ EXPIRADA ◀────┘
```

| From \ To | ACTIVA | CONSUMIDA | EXPIRADA | REVOCADA |
|---|---|---|---|---|
| EMITIDA | ✅ | ❌ | ✅ | ✅ |
| ACTIVA | ❌ | ✅ | ✅ | ✅ |
| CONSUMIDA | ❌ | ❌ (RN-06) | ❌ | ❌ |
| EXPIRADA | ❌ | ❌ | ❌ | ❌ |
| REVOCADA | ❌ | ❌ | ❌ | ❌ |

Every transition (accepted or rejected) creates a row in `transiciones_credencial` (FR-018,
FR-019). So that rejected ones are not lost to a rollback, the aggregate's transition methods
**do not raise exceptions**: they return a `TransitionResult(aceptada, estado_actual)` and record
the transition; the service commits and then decides the response. Expiration is applied lazily
(on read, if `now() ≥ expira_at` and the state is not final) and by the dispatcher's sweep.

### `transiciones_credencial` (insert only)

| Field | Type | Rules |
|---|---|---|
| `id` | bigint identity PK | |
| `credencial_id` | uuid FK → `credenciales_acceso.id` | NOT NULL, index |
| `estado_anterior` | `estado_credencial` | NULL for creation |
| `estado_solicitado` | `estado_credencial` | NOT NULL |
| `aceptada` | boolean | NOT NULL |
| `motivo` | text | e.g. `EMISION`, `RENOVACION`, `EXPIRACION`, `CONSUMO`, `TRANSICION_INVALIDA` |
| `actor` | text | `sistema`, `checkpoint:<id>`, etc. |
| `created_at` | timestamptz | |

- `BEFORE UPDATE OR DELETE` trigger that raises an exception (immutable history).

### `outbox_eventos` (the spec's `EventoPendiente` entity)

| Field | Type | Rules |
|---|---|---|
| `id` | uuid PK | Event id and `deduplication_id` in QStash |
| `tipo` | text | e.g. `credencial.emitida` |
| `version` | smallint | Event schema version (starts at 1) |
| `payload` | jsonb | Validated against `contracts/events/<tipo>.v<version>.json` |
| `estado` | `estado_evento` | default `PENDIENTE` |
| `intentos` | int | default 0 |
| `proximo_intento_at` | timestamptz | default `now()`; backoff `min(2^intentos, 180)` s — with the dispatcher every 60 s, the worst case after recovery is 4 min (< 5 min of SC-006) |
| `ultimo_error` | text | NULL |
| `created_at`, `entregado_at` | timestamptz | |

- Partial index `(proximo_intento_at) WHERE estado = 'PENDIENTE'`.

## Vercel Blob (private store)

- Pathnames:
  - Document photo: `documentos/{pasajero_id}/rostro-{random_suffix}.{jpg|png|webp}`.
  - Selfies: `selfies/{pasajero_id}/{intento_id}-{random_suffix}.{jpg|png|webp}`. A selfie whose
    `intento_id` does not exist in `intentos_verificacion` is an orphan (spec edge case).
- Max size 4 MB per image; types `image/jpeg`, `image/png`, `image/webp` (validated by header
  and by byte signature).
- Retention (spec assumption, pending confirmation with legal): while the identity is active and
  up to 90 days after its revocation or after the last failed attempt. When a selfie is deleted
  `imagen_eliminada_at` is filled and the attempt is kept without an image.

## Upstash Redis

| Key | Value | TTL | Use |
|---|---|---|---|
| `qr:{jti}` | `ACTIVA` → `CONSUMIDA` | = credential lifetime (30–60 s) | The spec's `TokenUsoUnico` entity. Single use (FR-017, RN-06). `DEL` on revocation, always after the commit |
| `rl:passes:{pasajero_id}` | managed by `upstash-ratelimit` | 60 s window | 30 issuances/min (FR-020a) |
| `cb:{nombre}` | hash `{estado, fallos, abierto_hasta}` | 10 min | Shared circuit breaker state |

Redis is never the source of truth for the credential state: if a key is missing, the credential
is treated as unusable (safe default state).

## Relationships

```text
pasajeros 1 ──── * intentos_verificacion
pasajeros 1 ──── 0..1 identidades_digitales (ACTIVA)
intentos_verificacion 1 ──── 0..1 identidades_digitales
identidades_digitales 1 ──── * credenciales_acceso
pasajeros 1 ──── * credenciales_acceso
credenciales_acceso 1 ──── * transiciones_credencial
```

# Data Model: Persistencia del flujo de registro, verificación biométrica y QR

**Feature**: `001-persistencia-registro-qr` | **Date**: 2026-09-23

Tres almacenes, cada uno con una responsabilidad:

| Almacén | Qué guarda | Qué NO guarda |
|---|---|---|
| Neon Postgres | Pasajeros, intentos, identidades, credenciales, historial, outbox | Imágenes, estado efímero del QR |
| Vercel Blob (privado) | Bytes de las selfies | Metadatos de negocio |
| Upstash Redis | Token de uso único del QR, límite de emisión, estado del circuit breaker | Nada que no pueda reconstruirse o expirar |

Todas las tablas usan `created_at` / `updated_at` `timestamptz` en UTC. Los identificadores son
`uuid` (v7 generados en la aplicación, ordenables por tiempo) salvo que se indique otra cosa.

## Enumeraciones (tipos `enum` de Postgres, fuente única en `domain/`)

| Enum | Valores |
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

| Campo | Tipo | Reglas |
|---|---|---|
| `id` | uuid PK | |
| `clerk_user_id` | text | NOT NULL, UNIQUE (una cuenta ↔ un pasajero) |
| `nombre_completo` | text | NOT NULL, 2–200 caracteres |
| `tipo_documento` | `tipo_documento` | NOT NULL |
| `numero_documento` | text | NOT NULL, 4–20 caracteres alfanuméricos, normalizado a mayúsculas sin separadores |
| `fecha_vencimiento_documento` | date | NOT NULL, debe ser ≥ hoy al registrar (FR-003) |
| `foto_documento_blob_pathname` | text | NOT NULL; referencia privada a la foto del rostro del documento (FR-001a) |
| `foto_documento_blob_url` | text | NOT NULL; URL privada, nunca el contenido |
| `estado` | `estado_pasajero` | NOT NULL, default `PENDIENTE_VERIFICACION` (FR-002) |
| `intentos_fallidos` | smallint | NOT NULL, default 0, CHECK 0–3 |
| `created_at`, `updated_at` | timestamptz | |

- `UNIQUE (tipo_documento, numero_documento)` (FR-004).

**Transiciones del pasajero**:

```text
PENDIENTE_VERIFICACION ──intento EXITOSO──────────────▶ VERIFICADO
PENDIENTE_VERIFICACION ──3er intento FALLIDO──────────▶ REQUIERE_REVISION_MANUAL
PENDIENTE_VERIFICACION ──FALLIDO (<3) / NO_CONCLUYENTE─▶ PENDIENTE_VERIFICACION
```

`VERIFICADO` y `REQUIERE_REVISION_MANUAL` no aceptan nuevas selfies en este alcance. La salida de
`REQUIERE_REVISION_MANUAL` la definirá el módulo de la consola del agente (fuera de alcance).

### `intentos_verificacion`

| Campo | Tipo | Reglas |
|---|---|---|
| `id` | uuid PK | |
| `pasajero_id` | uuid FK → `pasajeros.id` | NOT NULL, índice |
| `selfie_blob_pathname` | text | NULL solo si la imagen ya se eliminó por retención |
| `selfie_blob_url` | text | URL privada del blob, nunca el contenido (FR-005) |
| `resultado` | `resultado_intento` | NOT NULL |
| `motivo_fallo` | `motivo_fallo` | NULL salvo cuando `resultado = FALLIDO` (CHECK) |
| `score_liveness` | numeric(4,3) | NULL si `NO_CONCLUYENTE`; CHECK 0–1 |
| `score_comparacion` | numeric(4,3) | NULL si `NO_CONCLUYENTE`; CHECK 0–1 |
| `umbral_liveness` | numeric(4,3) | NOT NULL (valor vigente al evaluar) |
| `umbral_comparacion` | numeric(4,3) | NOT NULL |
| `proveedor` | text | NOT NULL (p. ej. `mock`, `vision`) |
| `imagen_eliminada_at` | timestamptz | NULL hasta que la retención borre el blob |
| `created_at` | timestamptz | |

**Regla del resultado** (vive solo en `domain/verification.py`):
`EXITOSO` ⇔ `score_liveness ≥ umbral_liveness` **y** `score_comparacion ≥ umbral_comparacion`.
Si falla la prueba de vida, `motivo_fallo = LIVENESS` (tiene prioridad); si no, `COMPARACION`.

### `identidades_digitales`

| Campo | Tipo | Reglas |
|---|---|---|
| `id` | uuid PK | |
| `pasajero_id` | uuid FK → `pasajeros.id` | NOT NULL |
| `intento_origen_id` | uuid FK → `intentos_verificacion.id` | NOT NULL, UNIQUE |
| `estado` | `estado_identidad` | NOT NULL, default `ACTIVA` |
| `revocada_at` | timestamptz | NULL |
| `created_at` | timestamptz | |

- Índice único parcial `(pasajero_id) WHERE estado = 'ACTIVA'` (FR-011).

### `credenciales_acceso`

| Campo | Tipo | Reglas |
|---|---|---|
| `id` | uuid PK | También es el `jti` del token firmado |
| `pasajero_id` | uuid FK → `pasajeros.id` | NOT NULL |
| `identidad_id` | uuid FK → `identidades_digitales.id` | NOT NULL |
| `codigo_vuelo` | text | NOT NULL, patrón `^[A-Z0-9]{2}[0-9]{1,4}[A-Z]?$` (p. ej. `AV9380`) |
| `permisos` | text[] | NOT NULL, no vacío; default `{embarque}` |
| `firma` | text | NOT NULL, firma Ed25519 (base64url) del token |
| `kid` | text | NOT NULL, id de la clave de firma usada |
| `emitida_at` | timestamptz | NOT NULL |
| `expira_at` | timestamptz | NOT NULL; CHECK `expira_at - emitida_at BETWEEN 30s AND 60s` (FR-016) |
| `estado` | `estado_credencial` | NOT NULL |
| `updated_at` | timestamptz | |

- Índice único parcial `(pasajero_id, codigo_vuelo) WHERE estado IN ('EMITIDA','ACTIVA')`
  (FR-020: como máximo una credencial viva por pasajero y vuelo).
- Índice `(estado, expira_at)` para el barrido de expiración.

**Máquina de estados** (patrón State en `domain/credential/states.py`; cada estado conoce sus
transiciones válidas, no hay `if` sueltos):

```text
          ┌────────────▶ REVOCADA ◀───────────┐
          │                                   │
      EMITIDA ───────────▶ ACTIVA ──────────▶ CONSUMIDA
          │                  │
          └──▶ EXPIRADA ◀────┘
```

| Desde \ Hacia | ACTIVA | CONSUMIDA | EXPIRADA | REVOCADA |
|---|---|---|---|---|
| EMITIDA | ✅ | ❌ | ✅ | ✅ |
| ACTIVA | ❌ | ✅ | ✅ | ✅ |
| CONSUMIDA | ❌ | ❌ (RN-06) | ❌ | ❌ |
| EXPIRADA | ❌ | ❌ | ❌ | ❌ |
| REVOCADA | ❌ | ❌ | ❌ | ❌ |

Toda transición (aceptada o rechazada) genera una fila en `transiciones_credencial` (FR-018,
FR-019). Para que las rechazadas no se pierdan por un rollback, los métodos de transición del
agregado **no lanzan excepciones**: devuelven un `TransitionResult(aceptada, estado_actual)` y
registran la transición; el servicio hace commit y luego decide la respuesta. La expiración se aplica de forma perezosa (al leer, si `now() ≥ expira_at` y el estado
no es final) y por el barrido del dispatcher.

### `transiciones_credencial` (solo inserción)

| Campo | Tipo | Reglas |
|---|---|---|
| `id` | bigint identity PK | |
| `credencial_id` | uuid FK → `credenciales_acceso.id` | NOT NULL, índice |
| `estado_anterior` | `estado_credencial` | NULL para la creación |
| `estado_solicitado` | `estado_credencial` | NOT NULL |
| `aceptada` | boolean | NOT NULL |
| `motivo` | text | p. ej. `EMISION`, `RENOVACION`, `EXPIRACION`, `CONSUMO`, `TRANSICION_INVALIDA` |
| `actor` | text | `sistema`, `checkpoint:<id>`, etc. |
| `created_at` | timestamptz | |

- Trigger `BEFORE UPDATE OR DELETE` que lanza una excepción (historial inmutable).

### `outbox_eventos` (entidad `EventoPendiente` del spec)

| Campo | Tipo | Reglas |
|---|---|---|
| `id` | uuid PK | Id del evento y `deduplication_id` en QStash |
| `tipo` | text | p. ej. `credencial.emitida` |
| `version` | smallint | Versión del esquema del evento (empieza en 1) |
| `payload` | jsonb | Validado contra `contracts/events/<tipo>.v<version>.json` |
| `estado` | `estado_evento` | default `PENDIENTE` |
| `intentos` | int | default 0 |
| `proximo_intento_at` | timestamptz | default `now()`; backoff `min(2^intentos, 180)` s — con el dispatcher cada 60 s, el peor caso tras la recuperación es 4 min (< 5 min de SC-006) |
| `ultimo_error` | text | NULL |
| `created_at`, `entregado_at` | timestamptz | |

- Índice parcial `(proximo_intento_at) WHERE estado = 'PENDIENTE'`.

## Vercel Blob (store privado)

- Pathnames:
  - Foto del documento: `documentos/{pasajero_id}/rostro-{sufijo_aleatorio}.{jpg|png|webp}`.
  - Selfies: `selfies/{pasajero_id}/{intento_id}-{sufijo_aleatorio}.{jpg|png|webp}`. Una selfie
    cuyo `intento_id` no existe en `intentos_verificacion` es huérfana (edge case del spec).
- Tamaño máximo 4 MB por imagen; tipos `image/jpeg`, `image/png`, `image/webp` (validados por
  cabecera y por firma de bytes).
- Retención (supuesto del spec, pendiente de confirmar con legal): mientras la identidad esté
  activa y hasta 90 días tras su revocación o tras el último intento fallido. Al borrar una selfie
  se llena `imagen_eliminada_at` y el intento se conserva sin imagen.

## Upstash Redis

| Clave | Valor | TTL | Uso |
|---|---|---|---|
| `qr:{jti}` | `ACTIVA` → `CONSUMIDA` | = vida de la credencial (30–60 s) | Entidad `TokenUsoUnico` del spec. Uso único (FR-017, RN-06). `DEL` al revocar, siempre después del commit |
| `rl:passes:{pasajero_id}` | gestionado por `upstash-ratelimit` | 60 s de ventana | 30 emisiones/min (FR-020a) |
| `cb:{nombre}` | hash `{estado, fallos, abierto_hasta}` | 10 min | Estado compartido del circuit breaker |

Redis nunca es la fuente de verdad del estado de la credencial: si una clave falta, la
credencial se trata como no usable (estado seguro por defecto).

## Relaciones

```text
pasajeros 1 ──── * intentos_verificacion
pasajeros 1 ──── 0..1 identidades_digitales (ACTIVA)
intentos_verificacion 1 ──── 0..1 identidades_digitales
identidades_digitales 1 ──── * credenciales_acceso
pasajeros 1 ──── * credenciales_acceso
credenciales_acceso 1 ──── * transiciones_credencial
```

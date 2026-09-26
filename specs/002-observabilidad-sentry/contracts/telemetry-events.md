# Contrato: eventos de telemetría y lista blanca

**Feature**: 002 · Referencias: [research.md](../research.md) §5–§7 · FR-003, FR-006, FR-008, FR-010–FR-014

Fuente única en código: `src/aeropass/observability/telemetry_catalog.py`. El `SentryAuditSink` lo usa para emitir y el `SentryPrivacyFilter` para filtrar. `tests/contract/test_telemetry_allowlist.py` falla si se emite un atributo que no esté aquí.

## 1. Auditoría → log de Sentry

Cada evento de auditoría registrado produce **un** log `info` con cuerpo `aeropass.audit`, salvo las excepciones de la columna "Log". Atributos:

| Atributo | Siempre | Valor |
|---|---|---|
| `aeropass.event` | sí | nombre del evento |
| `aeropass.outcome` | sí | `ok` \| `error` |
| `aeropass.error_type` | si `error` | nombre del tipo de excepción |
| `aeropass.resultado` | según evento | enum `ResultadoIntento` |
| `aeropass.motivo` | según evento | enum `MotivoFallo` |
| `aeropass.estado` | según evento | `VERIFICADO` \| `REQUIERE_REVISION_MANUAL` |
| `aeropass.dependencia` | según evento | nombre del circuit breaker |
| `aeropass.fault` | según evento | nombre del fallo inyectado (spec 003): `blob_down`, `mxface_down`, `mxface_slow`, `mxface_quota`, `db_down`, `redis_down`, `signing_down`, `qstash_down`, `slow` |

| Evento | Emisor | Log | Atributos extra | Métricas |
|---|---|---|---|---|
| `identity.verification` | `IdentityVerificationFacade.verify_and_create_identity` | siempre | `resultado`, `motivo`, `estado` (de `describe`) | `aeropass.verificacion.intento`; `aeropass.pasajero.estado_final` si hay `estado` |
| `credential.issue` | `PassIssuanceService.issue` | siempre | — | — |
| `credential.consume` | `CredentialLifecycleService.consume` | siempre | — | — |
| `auth.authenticate` | `ClerkAuthenticator` / `FakeAuth` | **solo `error`** (una por request: el `ok` sería ruido y costo) | — | — |
| `resilience.circuit_opened` | `CircuitBreaker` (apertura y reapertura) | siempre (nivel `warning`) | `dependencia` | `aeropass.circuit_breaker.apertura` |
| `fault.injected` | `adapters/faults` (spec 003), la primera vez que cada fallo pedido con `X-AeroPass-Fault` se dispara en una petición. Solo con `FAULT_INJECTION_ENABLED`, nunca en producción | siempre (nivel `warning`); además la etiqueta `fault_injected` en el scope de Sentry de la petición | `fault` | — |

Un evento que no esté en esta tabla no se envía (lista blanca). Para agregar uno: fila aquí, entrada en el catálogo, prueba.

**Estos logs no son el registro de auditoría del KR A2.7.** Sirven para diagnóstico y métricas; Sentry no garantiza que el registro esté completo ni que sea inmutable (research §14).

## 2. Métricas

| Nombre | Tipo | Unidad | Atributos permitidos |
|---|---|---|---|
| `aeropass.verificacion.intento` | count | — | `aeropass.resultado`, `aeropass.motivo` |
| `aeropass.pasajero.estado_final` | count | — | `aeropass.estado` |
| `aeropass.circuit_breaker.apertura` | count | — | `aeropass.dependencia` |

Todas llevan además `environment` y `release`, que agrega el SDK.

## 3. Logs de la librería `logging`

Solo se envían como logs de Sentry los registros de nivel `WARNING` o más de loggers cuyo nombre empieza con `aeropass.`; los de httpx, sqlalchemy, asyncpg y otros no. Sus mensajes ya están cubiertos por `tests/unit/test_no_pii_in_logs.py`. `logger.exception(...)` (nivel `ERROR`) crea además un evento de error, como hoy hace el despachador del outbox.

## 4. Lo que nunca se envía (FR-006)

Número, nombre o imagen de documento; selfie o bytes de imagen; puntajes biométricos (`score_*`); token firmado o payload del QR; clave de firma; cabeceras `Authorization` o de proveedores; `clerk_user_id`; `pasajero_id` como atributo o usuario; cuerpos de request; query strings.

# Data Model: Observabilidad con Sentry (Backend)

**Feature**: 002 · **Plan**: [plan.md](plan.md)

No hay entidades persistidas nuevas ni migraciones. Este documento describe los datos **de telemetría** que salen del proceso y los tipos nuevos en código.

## 1. Evento de auditoría (`emit_audit`)

Lo emite `@audited(event, describe=None)` o `emit_audit(event, **data)`, y lo reciben los hooks registrados.

| Campo | Tipo | Regla |
|---|---|---|
| `event` | `str` | Nombre del catálogo (`telemetry_catalog.AuditEvent`) |
| `outcome` | `"ok"` \| `"error"` | Lo pone `@audited` |
| `error` | `str` | Solo si `outcome = error`: el **nombre del tipo** de excepción, nunca su mensaje |
| atributos de `describe` | `str` \| `None` | Solo si `outcome = ok`; valores de enums del dominio. `None` = atributo ausente |

Eventos del catálogo (detalle en [contracts/telemetry-events.md](contracts/telemetry-events.md)): `identity.verification`, `credential.issue`, `credential.consume`, `auth.authenticate` y el nuevo `resilience.circuit_opened`.

## 2. Descripción del resultado de verificación

Valor que `describe` produce para `identity.verification` a partir de `VerificationOutcome`:

| Atributo | Origen | Valores |
|---|---|---|
| `resultado` | `outcome.intento.resultado` | `EXITOSO`, `FALLIDO`, `NO_CONCLUYENTE` |
| `motivo` | `outcome.intento.motivo_fallo` | `LIVENESS`, `COMPARACION` o ausente |
| `estado_final` | `outcome.pasajero.estado_final` | `VERIFICADO`, `REQUIERE_REVISION_MANUAL` o ausente |

### `Pasajero.estado_final` (nueva propiedad de dominio)

- Devuelve `estado` si es `VERIFICADO` o `REQUIERE_REVISION_MANUAL`; en otro caso, `None`.
- Es la fuente única de "estado final" (Principio IV).
- Como `apply_outcome` exige `PENDIENTE_VERIFICACION` antes de cada intento, un `estado_final` no nulo en el resultado significa que **este** intento produjo la transición. Cada pasajero la produce una sola vez.

## 3. Métricas (contadores, sin muestreo)

| Métrica | Se emite cuando | Atributos |
|---|---|---|
| `aeropass.verificacion.intento` | `identity.verification` ok | `aeropass.resultado`, `aeropass.motivo` (si existe) |
| `aeropass.pasajero.estado_final` | `identity.verification` ok con `estado_final` | `aeropass.estado` |
| `aeropass.circuit_breaker.apertura` | `resilience.circuit_opened` | `aeropass.dependencia` (`biometric`, `qstash`) |

Fórmulas derivadas:
- **Autoservicio** = `sum(estado_final{estado=VERIFICADO})` ÷ `sum(estado_final)`.
- **Auto rechazo** = `sum(intento{resultado=FALLIDO})` ÷ `sum(intento{resultado∈{EXITOSO,FALLIDO}})`, separado por `aeropass.motivo`.

## 4. Span de paso

| Campo | Regla |
|---|---|
| `op` | `aeropass.step` para `@traced`/`span()` |
| `name` | Nombre del decorador (p. ej. `passes.issue`, `circuit_breaker.biometric`) |
| `status` | `ok`; `deadline_exceeded` (TimeoutError); `unavailable` (CircuitOpenError); `cancelled` (CancelledError); `internal_error` (cualquier otra) |

La excepción se relanza sin cambios: el span nunca altera el flujo.

## 5. Chequeo de salud

| Tipo | Campos |
|---|---|
| `HealthCheck` (puerto, `Protocol`) | `name: str`; `async check() -> None` (lanza si falla) |
| `HealthReport` | `ok: bool`, `fallidos: tuple[str, ...]` (nombres de checks; **solo** para logs internos, nunca en la respuesta HTTP) |

Transición: todos los checks terminan bien dentro de su timeout → `ok=True` (HTTP 200). Cualquiera falla o se pasa de tiempo → `ok=False` (HTTP 503).

## 6. Configuración nueva (`Settings`)

| Variable | Tipo | Por defecto | Regla |
|---|---|---|---|
| `SENTRY_DSN` | `str` | `""` | Vacío = observabilidad apagada (FR-004) |
| `SENTRY_ENVIRONMENT` | `str` | `dev` | `prod`, `demo`, `dev` o `simulated` |
| `SENTRY_TRACES_SAMPLE_RATE` | `float` | `1.0` | Rango [0, 1]; se valida al arrancar |
| `VERCEL_GIT_COMMIT_SHA` | `str` | `""` | Release; la inyecta Vercel |

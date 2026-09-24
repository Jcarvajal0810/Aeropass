# Implementation Plan: Observabilidad con Sentry (Backend)

**Branch**: `observabilidad` | **Date**: 2026-09-24 | **Spec**: [spec.md](spec.md)

**Input**: Feature specification from `/specs/002-observabilidad-sentry/spec.md`

## Summary

Conectar los hooks `@traced` y `@audited` que ya existen a Sentry para que el backend:
1. capture toda excepción no controlada sin datos sensibles;
2. muestre una traza por paso del pipeline y por llamada externa;
3. exponga la tasa de autoservicio (KR A1.2), el auto rechazo, la disponibilidad, la latencia de pase y las aperturas de circuit breaker;
4. tenga dashboard y alertas por correo en el proyecto `aeropass-back`, más una forma reproducible de preparar la presentación de 15 minutos.

Enfoque técnico (detalle en [research.md](research.md)):
- **SDK**: `sentry-sdk[fastapi]` 2.70, inicializado de forma idempotente en `create_app`. En Vercel, el envío pendiente se vacía con `wait_until` después de responder.
- **Privacidad**: `send_default_pii=False`, sin variables locales ni cuerpos de request, y un único `SentryPrivacyFilter` con lista blanca en los cinco puntos de salida del SDK.
- **Métricas de negocio**: el decorador `@audited` gana un `describe` opcional; el facade declara resultado, motivo y estado final después del `commit`. Un sink de Sentry convierte los eventos de auditoría en logs y en contadores sin muestreo.
- **Técnicas**: nuevo `GET /health` (base de datos + Redis) vigilado por un monitor de Uptime; latencia desde las transacciones de FastAPI; el circuit breaker audita cada apertura y corre dentro de un span con estado.
- **Operación**: dashboard y alertas creados a mano según [contracts/dashboard-and-alerts.md](contracts/dashboard-and-alerts.md); herramienta `seed_demo` para la presentación.
- **Catálogo de fallos (anexo C)**: la inyección sigue diferida, pero cada hipótesis que toca este backend (F3, F4, F7, F8, F11, F18) ya tiene su señal y su widget (research §13).
- **Fuera de alcance**: la auditoría enviada a Sentry no cumple el KR A2.7, el registro inmutable del 100% de las validaciones (research §14).

## Technical Context

**Language/Version**: Python 3.11+ (proyecto `requires-python >=3.11`; las pruebas se corren con 3.11 porque `pgserver` no tiene wheels para 3.14)

**Primary Dependencies**:
- Nueva: `sentry-sdk[fastapi]>=2.70,<3`.
- Existentes: FastAPI, SQLAlchemy async + asyncpg, `upstash-redis` y `vercel` 0.11.3 (este último aporta `wait_until`).

**Storage**: N/A. No hay tablas ni migraciones nuevas; las métricas salen de eventos que ya ocurren. `/health` solo lee (`SELECT 1`, `PING`).

**Testing**:
- pytest + pytest-asyncio.
- Unitarias del filtro de privacidad, los sinks y la inicialización. Usan un transporte de Sentry en memoria: sin red ni DSN real.
- Prueba de contrato: los atributos emitidos coinciden con la lista blanca.
- Integración (Postgres real) para facade, breaker y `/health`.
- Verificación manual en Sentry: quickstart §3–§6.

**Target Platform**: Vercel, runtime serverless de Python, región `gru1`.

**Project Type**: web-service (API FastAPI).

**Performance Goals**: latencia añadida imperceptible (SC-004); ningún envío ocurre antes de la respuesta; `/health` responde en menos de 3 s incluso con una dependencia caída.

**Constraints**:
- Cero datos sensibles en Sentry (FR-006).
- 100% de errores, logs de la lista blanca y métricas; trazas muestreadas (prod 0.2).
- Sin DSN, comportamiento idéntico al actual.
- Cold starts frecuentes: sin estado en memoria que importe (Principio I).

**Scale/Scope**:
- 7 servicios y adaptadores ya instrumentados (~25 puntos `@traced`/`@audited`), 1 endpoint nuevo, 3 contadores, ~6 tipos de log de auditoría.
- En Sentry: 1 dashboard de 11 widgets, 4 reglas de alerta y 1 monitor de Uptime.

## Constitution Check

*GATE: Must pass before Phase 0 research. Re-check after Phase 1 design.*

Evaluado contra la **constitución 1.1.0** (enmienda aprobada por el usuario el 2026-09-24).

| Principio / patrón | Estado | Cómo se cumple |
|---|---|---|
| I. Stack canónico | ✅ | La 1.1.0 aclara que el SDK de Sentry cumple "OpenTelemetry + Sentry" mientras toda la instrumentación pase por los hooks. Migrar a OTel solo cambia el sink (research §1). Idempotente ante cold starts (research §2). |
| II. Alcance acotado | ✅ | La 1.1.0 incluye la observabilidad en el alcance: sinks en `adapters/observability/`, `/health` y la herramienta de demo. `validate`, la consola y `flights` siguen fuera; los servicios solo ganan anotaciones declarativas. |
| III. SOLID | ✅ | Sinks, filtro y checks de salud son clases de una sola responsabilidad. `HealthService` recibe `HealthCheck` por abstracción. Una métrica nueva se agrega con `describe` o con una entrada en el mapeo del sink, sin modificar clases existentes. |
| IV. DRY | ✅ | Qué estados son finales vive en `Pasajero.estado_final`; los nombres de eventos, métricas y atributos, en un solo módulo (`telemetry_catalog.py`) que usan sink, filtro y prueba de contrato. |
| V. Circuit breaker | ✅ | Sin cambios de comportamiento; ahora cada apertura (incluida la reapertura desde `HALF_OPEN`) se audita y cada llamada corre en un span con estado. Los checks de salud tienen timeout explícito; DB y Redis no están en la lista de dependencias inestables. |
| VI. Observabilidad detrás de los hooks | ✅ | Solo `adapters/observability/` importa `sentry_sdk`. Los hooks se extienden sin romperlos (`span()` público, `describe` opcional). La telemetría no bloquea (flush con `wait_until`, research §3) y el filtro con lista blanca excluye los datos prohibidos (research §4). La inyección de fallos sigue fuera de alcance. |
| Patrón Decorator | ✅ | `@audited(describe=...)` agrega métricas sin modificar la lógica del facade. |
| Patrón Singleton | ✅ | El cliente de Sentry es global por proceso; `/health` reutiliza el pool de Neon y el cliente de Redis existentes. |
| Patrón Factory / mocks | ✅ | Sin DSN el sink no se registra; en pruebas, un transporte en memoria reemplaza la red. `seed_demo` usa los adaptadores `fake` existentes. |
| Revisión: hooks presentes | ✅ | Todo punto nuevo (breaker, `/health`) queda instrumentado. |
| Seguridad | ✅ | DSN por variable de entorno en Vercel, nunca en el repo; `SENTRY_AUTH_TOKEN` no se usa. `/health` no revela versiones, hosts ni errores. |

**Re-check post-diseño (Phase 1)**: todo en verde contra la 1.1.0. Los contratos no agregan tablas, estado en memoria ni tipos de Sentry en `domain/` o `services/`. La enmienda y la desviación D1 quedaron aprobadas por el usuario el 2026-09-24; no hay puntos abiertos antes de `/speckit-tasks`.

## Project Structure

### Documentation (this feature)

```text
specs/002-observabilidad-sentry/
├── plan.md                         # este archivo
├── research.md                     # Phase 0
├── data-model.md                   # Phase 1
├── quickstart.md                   # Phase 1
├── contracts/
│   ├── telemetry-events.md         # auditoría → logs y métricas, lista blanca
│   ├── privacy-filter.md           # SentryPrivacyFilter
│   ├── health-endpoint.md          # GET /health
│   └── dashboard-and-alerts.md     # widgets, reglas, monitor de Uptime
└── tasks.md                        # Phase 2 (/speckit-tasks)
```

### Source Code (repository root)

```text
src/aeropass/
├── main.py                                   # MODIFICA: configure_observability() antes de FastAPI(); middleware de flush; router health
├── config.py                                 # MODIFICA: sentry_dsn, sentry_environment, sentry_traces_sample_rate, release
├── observability/
│   ├── hooks.py                              # MODIFICA: span() público; audited(describe=...)
│   └── telemetry_catalog.py                  # NUEVO: nombres de eventos, métricas y atributos (fuente única)
├── adapters/
│   ├── observability/
│   │   ├── __init__.py                       # NUEVO
│   │   ├── sentry_setup.py                   # NUEVO: init idempotente, sampler, FlushTelemetryMiddleware
│   │   ├── sentry_privacy.py                 # NUEVO: SentryPrivacyFilter
│   │   └── sentry_sinks.py                   # NUEVO: SentrySpanHook, SentryAuditSink
│   ├── health/
│   │   ├── __init__.py                       # NUEVO
│   │   └── checks.py                         # NUEVO: DatabaseHealthCheck, RedisHealthCheck
│   └── resilience/circuit_breaker.py         # MODIFICA: span por llamada + auditoría de apertura
├── ports/health.py                           # NUEVO: puerto HealthCheck
├── services/
│   ├── health_service.py                     # NUEVO: corre los checks en paralelo con timeout
│   └── identity_verification_facade.py       # MODIFICA: @audited(..., describe=...)
├── domain/passenger.py                       # MODIFICA: propiedad estado_final
├── api/
│   ├── deps.py                               # MODIFICA: health_service en el Container
│   └── routers/health.py                     # NUEVO: GET /health
└── tools/seed_demo.py                        # NUEVO: datos de la demo (research §12)

tests/
├── unit/
│   ├── test_sentry_privacy.py                # NUEVO (primero)
│   ├── test_sentry_sinks.py                  # NUEVO
│   ├── test_observability_setup.py           # NUEVO: sin DSN = no-op; idempotencia
│   ├── test_hooks.py                         # NUEVO: span(), describe
│   ├── test_health_service.py                # NUEVO
│   └── test_circuit_breaker.py               # MODIFICA: auditoría de apertura y reapertura
├── contract/
│   ├── test_telemetry_allowlist.py           # NUEVO: atributos emitidos ⊆ lista blanca
│   └── test_health_contract.py               # NUEVO
└── integration/
    └── test_verification_telemetry.py        # NUEVO: resultado/motivo/estado_final tras commit

pyproject.toml                                # MODIFICA: sentry-sdk[fastapi]
.env.example                                  # MODIFICA: SENTRY_DSN, SENTRY_ENVIRONMENT, SENTRY_TRACES_SAMPLE_RATE
README.md                                     # MODIFICA: sección de observabilidad; quitar "Full observability" de pendientes
.specify/memory/constitution.md               # HECHO: versión 1.1.0 (enmienda II y VI, aclaración del I)
```

**Structure Decision**: un solo servicio FastAPI con la organización existente (`domain/`, `ports/`, `services/`, `adapters/`, `api/`). Todo lo que importa `sentry_sdk` vive en `adapters/observability/`. Dominio y servicios solo conocen `observability/hooks.py` y el catálogo. La configuración de Sentry es manual y queda documentada en `contracts/dashboard-and-alerts.md`.

## Complexity Tracking

| Violación / desviación | Por qué hace falta | Alternativa más simple descartada porque |
|---|---|---|
| **SDK de Sentry sin OpenTelemetry** (Principio I y anexo B piden OTel hacia un backend externo) | Entregar antes de la presentación, con la misma configuración y las mismas consultas que la app | OTel con Sentry como destino: más configuración, un segundo modelo de spans y menos madurez en Python. **Decisión del usuario (2026-09-24); la constitución 1.1.0 lo admite en el Principio I.** Migrar después no toca servicios: basta cambiar el sink de los hooks. |
| **Enmienda de los Principios II y VI** (observabilidad pasa a estar en el alcance de este repo), **con aclaración del I** (SDK de Sentry detrás de los hooks) | La feature implementa en este repo lo que la constitución asigna a "otro equipo". La Gobernanza exige enmienda para cambios de alcance. | Implementarla en otro repo: los sinks deben registrarse dentro del proceso del backend y `/health` debe ser una ruta de esta app. **Aprobada por el usuario (2026-09-24): constitución 1.1.0.** |
| Extender `observability/hooks.py` (`span()` público, `describe` en `@audited`) | El breaker necesita un span con nombre dinámico, y el facade tiene que declarar el resultado después del `commit` | Emitir desde dentro de los servicios: se emitiría antes del `commit` o mezclaría telemetría con lógica (research §6–§7). |
| **D1 — Alerta de autoservicio sin el mínimo de 10 pasajeros** | Sentry no permite condicionar un monitor al volumen | Un evaluador programado contra la API de Sentry es código nuevo y contradice FR-018. **Aceptada por el usuario (2026-09-24), igual que en la app; FR-017 ajustado.** |
| Middleware de flush con `wait_until` | En serverless, la cola del SDK puede perderse al congelarse el proceso | Flush síncrono: suma hasta 2 s por respuesta (research §3). |

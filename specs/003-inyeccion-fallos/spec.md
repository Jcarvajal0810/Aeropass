# 003 — Inyección de fallos por petición (`X-AeroPass-Fault`)

**Estado:** implementado en la rama `feature/003-fault-injection` · **Fecha:** 2026-09-25 · **Relacionado:** spec 002 (observabilidad), anexo de inyección de fallos, app `AeroPass-App/specs/015-observabilidad-sentry/fault-injection.md`

## 1. Objetivo

Que una petición pida al backend que **falle una dependencia concreta solo para esa petición**, con el header `X-AeroPass-Fault`, para provocar fallos reales (Blob, proveedor biométrico, base de datos, Redis, firma, QStash) sin redesplegar, y comprobar que la observabilidad de la spec 002 reacciona (W5–W11, B1, B3, B4). La app los pide desde su build `chaos` (botón ⚡).

## 2. Restricción principal

**Nunca en producción.** Solo existe con `FAULT_INJECTION_ENABLED=true`, y `Settings` se niega a cargar con esa bandera si `VERCEL_ENV=production` o `SENTRY_ENVIRONMENT=prod` (el backend no arranca). Se usa en un **Preview de Vercel** con su propia rama de Neon, su propio Upstash y su propio Blob.

## 3. Catálogo de fallos y respuestas verificadas

Varios fallos se combinan con coma (`blob_down,redis_down`). La columna "Respuesta" es la que verifican los tests de integración (`tests/integration/test_fault_injection.py`).

| Valor | Dónde se inyecta | Respuesta de la API | Señales (spec 002) |
|---|---|---|---|
| `blob_down` | Cliente de Vercel Blob (debajo de `VercelBlobStorage`, que lo convierte en `MediaUnavailable`); en modo fake, el almacén en memoria | Registro y selfie: `503 ALMACENAMIENTO_NO_DISPONIBLE` con `Retry-After` | W2, W11 |
| `mxface_down` | MX Face y el proveedor de visión: el **transporte HTTP** responde un 503 real. El mock: dentro del breaker | `200` `NO_CONCLUYENTE`, sin gastar intento | W8, **W10 (503 con el host del proveedor)**; 5 en 60 s abren el breaker real → W7, **B3** |
| `mxface_slow:<ms>` | Proveedor (transporte HTTP o mock), espera `<ms>` (tope 60 000; sin valor, 12 000) | `NO_CONCLUYENTE` si supera `BIOMETRIC_TIMEOUT_SECONDS` | W9 (`deadline_exceeded`), W8 |
| `mxface_quota` | MX Face: el transporte HTTP responde un **429 real**. El mock: `ProviderUnavailable("provider status 429")` | `200` `NO_CONCLUYENTE` | **W10 (429 con el host del proveedor)**, W8 |
| `slow:<ms>` | Toda la petición espera `<ms>` (sin valor, 4 000) dentro de un paso llamado `fault.slow` | La respuesta normal, más lenta | **W6** (p95 y p99 de `/v1/passes` si se aplica ahí), **W9** (fila `fault.slow`) |
| `db_down` | Apertura de sesión de SQLAlchemy (`OperationalError`), en `uow()` y en `/health` | **Hoy no controlado (500)** → hallazgo §6 | W1, **B1**; `/health` → `503` |
| `redis_down` | Cliente de Upstash (debajo de limitador, token store, estado del breaker y `/health`) | Emisión de pase: `503 ALMACENAMIENTO_NO_DISPONIBLE`; el limitador deja pasar (fail-open) y la credencial se revoca porque el token no se pudo registrar | W11; `/health` → `503` |
| `signing_down` | `CredentialSigner.sign` | **Hoy no controlado (500)** → hallazgo §6 | W11, **B1** |
| `qstash_down` | Publicador de eventos dentro del breaker `qstash` | La verificación responde igual (`EXITOSO`); el outbox reintenta | Breaker `qstash`, W7 |

Nombres desconocidos: warning `fault_injection_unknown` en el log y la petición sigue normal.

## 4. Implementación

- **`config.py`**: `fault_injection_enabled`, `fault_injection_secret`, `vercel_env` y el validador `_fault_injection_never_in_production`.
- **`api/fault_injection.py`**: middleware ASGI puro, registrado en `create_app` solo con la bandera.
  - Si hay secreto, exige `X-AeroPass-Fault-Key` (comparación en tiempo constante; la clave nunca se registra).
  - Activa el plan en un `ContextVar`: alcance de petición, sin estado global.
  - Responde `X-AeroPass-Fault-Applied` con los fallos que se dispararon.
- **`adapters/faults/context.py`**: parseo del header, `trip(nombre)` y registro del fallo (una vez por fallo y petición).
- **`adapters/faults/wrappers.py`**: cada envoltorio lanza **la misma excepción que la dependencia real**, así que servicios y handlers responden como en un incidente de verdad, sin ramas especiales. Donde el adaptador real maneja la falla (Redis, Blob), el fallo se inyecta **debajo**, en el cliente, para que ese manejo corra igual que en producción.
- **`api/deps.py`**: construye los envoltorios **solo** con la bandera; sin ella no existen (cero costo). La sesión de base de datos se envuelve al usarse (`_sessions()`), así que los tests que reemplazan `session_factory` siguen funcionando.
- **`adapters/biometrics/factory.py`**: el envoltorio del proveedor va dentro de `ResilientBiometricProvider`, así que el timeout y la apertura del breaker reaccionan de verdad.

## 5. Observabilidad

**Visibilidad en el tablero (revisión del 26/09/2026).** Tres cambios para que cada fallo inyectado mueva su widget sin tocar el tablero:
- Los fallos del proveedor se responden **en el transporte HTTP** (`FaultInjectingTransport`) cuando el proveedor es MX Face o el de visión: la integración httpx de Sentry registra una llamada `http.client` real con su código (503 o 429) y el adaptador la maneja como una respuesta verdadera.
- `sentry-sdk` 2.70 no escribe `server.address` en las llamadas httpx, y W10 agrupa por ese campo: el filtro de privacidad lo completa con el host de la URL (solo el host). Esto también arregla W10 para el tráfico real.
- El fallo nuevo `slow:<ms>` hace visible la latencia: un paso `fault.slow` en W9 y la duración de la transacción en W6.

- Evento de auditoría **`fault.injected`** (nivel `warning`) con el atributo **`aeropass.fault`**, en el catálogo (`telemetry_catalog.py`) y en el contrato (`002/contracts/telemetry-events.md`).
- `SentryAuditSink` pone la etiqueta **`fault_injected`** en el scope de Sentry de la petición: errores y transacciones de esa petición llevan la marca.
- El Preview usa `SENTRY_ENVIRONMENT=chaos`. B1 y B3 se disparan en todos los entornos.

## 6. Hallazgos a decidir

Los tests los documentan tal como están hoy (`..._is_currently_unhandled`):
- **`db_down`**: una base de datos caída sale como error no controlado (500, B1). Propuesta: mapearlo a un `DomainError` `503` (por ejemplo `SERVICIO_NO_DISPONIBLE`, con `Retry-After`). La app trata cualquier 5xx como error técnico.
- **`signing_down`**: la emisión de pase debería responder un error controlado, no un 500.
- **`redis_down`**: confirmado el comportamiento seguro. El limitador es fail-open, y el pase **no** se emite si su token no se registra (sin ventana de reutilización).

## 7. Configuración del Preview

| Variable | Valor |
|---|---|
| `FAULT_INJECTION_ENABLED` | `true` |
| `FAULT_INJECTION_SECRET` | secreto largo y aleatorio (recomendado; la app lo envía como `X-AeroPass-Fault-Key` desde `FAULT_INJECTION_KEY` en su `env/chaos.env`) |
| `SENTRY_ENVIRONMENT` | `chaos` |
| `DATABASE_URL`, Upstash, Blob | recursos propios del Preview |
| `BIOMETRIC_PROVIDER` | `mock` o `mxface`, según el experimento |

Si el Preview tiene Deployment Protection, activar "Protection Bypass for Automation" (la app lo envía desde `VERCEL_PROTECTION_BYPASS`).

## 8. Tests

- `tests/unit/test_fault_injection.py`: parseo, `trip`, `FaultProxy`, el middleware con y sin secreto, y la guarda de producción.
- `tests/integration/test_fault_injection.py`: cada fallo por la API real, con su respuesta y su registro.
- `tests/integration/test_fault_injection_disabled.py`: con la bandera apagada el header no tiene efecto y no existe ningún envoltorio.
- `tests/contract/test_telemetry_allowlist.py`: incluye `fault.injected` y `aeropass.fault`.

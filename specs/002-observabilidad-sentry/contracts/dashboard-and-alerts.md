# Contrato: dashboard, alertas y monitor de Uptime del proyecto `aeropass-back`

**Feature**: 002 · Referencias: [research.md](../research.md) §5, §8, §11 · FR-016, FR-017, FR-018 · Historias 7 y 9

Se configuran **a mano** en la UI de Sentry (organización Aeropass, proyecto `aeropass-back`). Este archivo sirve para recrearlos. Si alguien cambia un umbral en la UI, la UI manda y este archivo se actualiza en el siguiente PR.

## Estado en Sentry

Pendiente: se completa con los IDs al configurarlo, igual que en la app.

## Monitor de Uptime

| Campo | Valor |
|---|---|
| URL | `https://<deploy de prod>/health` |
| Método | `GET` |
| Intervalo | 1 minuto |
| Timeout | 10 s |
| Tolerancia | 3 fallos seguidos abren el incidente; 1 éxito lo cierra |
| Entorno | `prod` |
| Notificación | correo al equipo `#aeropass-team` (alerta B4) |

## Dashboard "AeroPass Backend — Observabilidad"

Filtro global: **Environment** (`prod`, `demo`, `dev`, `simulated`); por defecto `prod`.

| # | Widget | Dataset | Consulta | Visualización |
|---|---|---|---|---|
| W1 | Excepciones no controladas | Errors | `count()` con `error.unhandled:true`, agrupado por `transaction` | Barras en el tiempo |
| W2 | Tasa de fallos por endpoint | Spans | `failure_rate()` con `is_transaction:true`, agrupado por `transaction` | Tabla |
| W3 | Autoservicio (KR A1.2, meta 85% o más) | Application Metrics | `sum(aeropass.pasajero.estado_final)` agrupado por `aeropass.estado`; si el widget admite ecuaciones, `VERIFICADO / total` | Número grande + tabla |
| W4 | Auto rechazo | Application Metrics | `sum(aeropass.verificacion.intento)` agrupado por `aeropass.resultado` y `aeropass.motivo` | Tabla + barras apiladas |
| W5 | Disponibilidad (KR A2.1, meta 99,9% o más) | Uptime | porcentaje del monitor de Uptime. Si el dashboard no tiene dataset de Uptime, un widget de texto con el enlace al monitor | Número grande |
| W6 | Latencia de pase | Spans | `p95(span.duration)` y `p99(span.duration)` con `is_transaction:true transaction:["POST /v1/passes","GET /v1/passes/{credencial_id}"]`, agrupado por `transaction` | Tabla + línea |
| W7 | Aperturas de circuit breaker (KR A2.5) | Application Metrics | `sum(aeropass.circuit_breaker.apertura)` agrupado por `aeropass.dependencia` | Barras en el tiempo |
| W8 | Verificaciones no concluyentes | Application Metrics | `sum(aeropass.verificacion.intento)` con `aeropass.resultado:NO_CONCLUYENTE` | Línea |
| W9 | Latencia de pasos del pipeline | Spans | `p95(span.duration)` con `span.op:aeropass.step`, agrupado por `span.description` | Tabla |
| W10 | Respuestas del proveedor biométrico | Spans | `count()` con `span.op:http.client` y `server.address` del proveedor, agrupado por `http.response.status_code` | Barras apiladas |
| W11 | Emisiones de pase fallidas | Logs | `count()` con `aeropass.event:credential.issue aeropass.outcome:error`, agrupado por `aeropass.error_type` | Tabla + línea |

### Relación con el catálogo de fallos (anexo C)

Cuando se ejecuten los experimentos, que siguen diferidos, estos widgets son los que verifican cada hipótesis (research §13):

| Fallo | Widgets / alertas |
|---|---|
| F3: proveedor facial caído | W7, W8, B3 |
| F4: proveedor facial lento | W9 (estado `deadline_exceeded`) |
| F18: cuota del proveedor agotada (429) | W10, W8 |
| F7: conmutación de la base de datos | W5, W1, B4 |
| F8: Redis de control de reuso caído | W5, W11 (`AlmacenamientoNoDisponible`) |
| F11: servicio de firma caído | W11, W1, B1 |

## Reglas de alerta (correo al equipo `#aeropass-team`)

| # | Regla | Tipo / condición | Periodo | Entornos | Frecuencia máx. |
|---|---|---|---|---|---|
| B1 | Excepción no controlada | Alerta sobre issues: cada evento con `error.unhandled:true` | Inmediata | Todos | 1 correo cada 5 min |
| B2 | Autoservicio bajo | Monitor de Application Metrics: `VERIFICADO / total < 0,85` (ecuación A/B) | 1 hora | Solo `prod` | 1 por hora |
| B3 | Contingencia: apertura de circuit breaker | Monitor de Application Metrics: `sum(aeropass.circuit_breaker.apertura) ≥ 1` | 10 min | Todos | 1 cada 10 min |
| B4 | Backend no disponible | Monitor de Uptime (3 fallos seguidos) | ~3 min | `prod` | Por incidente |

Todos los correos indican entorno y métrica, sin datos sensibles.

## Desviaciones conocidas

- **D1 (FR-017)**: B2 no puede exigir al menos 10 pasajeros en estado final en la hora. Evalúa la ventana tal cual; con poco volumen, revisar W3 antes de actuar. Igual que en la app. **Pendiente de confirmación del usuario.**
- Si los monitores de Application Metrics no admiten la ecuación A/B en B2, la alternativa es alertar sobre `REQUIERE_REVISION_MANUAL` como conteo absoluto (se decide al configurarlo, como A3 en la app).

## Configuración del proyecto (una sola vez)

- Security & Privacy: activar "Prevent Storing of IP Addresses" y dejar el Data Scrubbing por defecto.
- Variables en Vercel (prod): `SENTRY_DSN`, `SENTRY_ENVIRONMENT=prod`, `SENTRY_TRACES_SAMPLE_RATE=0.2`.

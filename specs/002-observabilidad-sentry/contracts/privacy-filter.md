# Contrato: `SentryPrivacyFilter`

**Feature**: 002 · Referencias: [research.md](../research.md) §4 · FR-006, SC-003

Módulo: `src/aeropass/adapters/observability/sentry_privacy.py`. Sus pruebas (`tests/unit/test_sentry_privacy.py`) se escriben **antes** que el código.

## Configuración del SDK (prerrequisito, probada en `test_observability_setup.py`)

| Opción | Valor |
|---|---|
| `send_default_pii` | `False` |
| `include_local_variables` | `False` |
| `max_request_body_size` | `"never"` |
| `before_send`, `before_send_transaction`, `before_breadcrumb`, `before_send_log`, `before_send_metric` | métodos del filtro |

## `before_send(event, hint)` — errores

1. Borra `user`, `request.cookies`, `request.data`, `request.query_string` y `request.env`; a `request.url` le quita la query.
2. `request.headers`: conserva solo `content-type` y `user-agent`.
3. En cada `exception.values[]`, si el `module` del tipo **no** empieza con `aeropass.`, reemplaza `value` por `[redactado]`. Esto incluye las excepciones de Python, cuyo `module` es nulo. Conserva `type` y `stacktrace` (con variables locales ya desactivadas en el SDK). Las excepciones `aeropass.*` conservan su mensaje, que son textos fijos.
4. `logentry` de eventos que vienen de `logging`: se conserva si el logger es `aeropass.*` (cubierto por `test_no_pii_in_logs.py`); con cualquier otro logger se reemplaza por `{"message": "[redactado]"}`.
5. Nunca retorna `None` para un error: el filtro limpia, no descarta.

**Contexto de código fuente**: el SDK adjunta a cada frame las líneas de código que lo rodean. Son código de la app, no datos de un request, y se conservan porque son clave para diagnosticar. Por eso el código no debe tener secretos escritos a mano, regla que ya vale sin Sentry.

## `before_send_transaction(event, hint)` — trazas

Aplica los pasos 1–2 al `request` de la transacción. En cada span, borra `data["http.query"]` y `data["http.fragment"]` y quita la query de `data["url"]`.

## `before_breadcrumb(crumb, hint)`

| Categoría | Acción |
|---|---|
| `http`, `httplib` | conserva `method`, `status_code` y `url` sin query; borra el resto de `data` |
| `query` (SQL) | conserva: la sentencia va con parámetros sin valores |
| `log` / logger `aeropass.*` | conserva |
| cualquier otra | descarta (`None`) |

## `before_send_log(log, hint)`

- Pasa si el log viene del `SentryAuditSink` (`aeropass.event` presente) **o** de un logger `aeropass.*`.
- Se quedan los atributos de la lista blanca ([telemetry-events.md](telemetry-events.md)) y los del SDK con prefijo `sentry.`, `server.`, `code.` y `logger.`. El resto se borra, incluidos `user.*`, `thread.*` y `process.*`.
- Cualquier otro log se descarta.

## `before_send_metric(metric, hint)`

Pasa solo si el nombre está en el catálogo de métricas; conserva solo sus atributos permitidos y los del SDK (`sentry.*`, `server.*`). El resto se descarta.

## Casos obligatorios de prueba

- Un error con `request.data = {"numero_documento": "..."}` sale sin `data`.
- Una `asyncpg` `UniqueViolationError` con el valor en el mensaje sale con `value = "[redactado]"` y conserva el tipo.
- Una `DomainError` conserva su mensaje.
- Una URL con `?token=x` pierde la query en el evento, el breadcrumb y el span.
- Un log con un atributo desconocido lo pierde; un log de `httpx` se descarta.
- Una métrica que no está en el catálogo se descarta.
- La cabecera `authorization` nunca sale.

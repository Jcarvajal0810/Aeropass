# Research: Observabilidad con Sentry (Backend)

**Feature**: 002 · **Plan**: [plan.md](plan.md) · **Fecha**: 2026-09-24 · **Contexto adicional**: recorrido del pasajero del equipo (arquitectura AWS/Vercel y anexo C)

Cada sección sigue el formato Decisión / Razón / Alternativas. Ninguna sección deja un `NEEDS CLARIFICATION` abierto.

## §1 SDK e integraciones

- **Decisión**: `sentry-sdk[fastapi]>=2.70,<3` (versión actual 2.70.0), inicializado una sola vez al crear la app. Integraciones: FastAPI/Starlette (errores y una transacción por ruta, nombrada con la plantilla, p. ej. `GET /v1/passes/{credencial_id}`), SQLAlchemy y httpx (spans de base de datos y llamadas externas), Logging (logs de nivel `WARNING` o más de los loggers `aeropass.*`). Logs y métricas activados (`enable_logs=True`; métricas activas por defecto).
- **Razón**: 2.70 ya trae `sentry_sdk.logger` y `sentry_sdk.metrics.count/distribution/gauge` con los hooks `before_send_log` y `before_send_metric`, las mismas señales que usó la app (spec 015). Así ambos proyectos se consultan igual en Sentry.
- **Alternativas**: OpenTelemetry como instrumentación con Sentry como backend. Es lo que piden el Principio I ("OpenTelemetry + Sentry") y el anexo B del documento final ("OpenTelemetry desde las funciones hacia un backend externo", paso 10 del recorrido del equipo). **Descartado en este ciclo por decisión del usuario (2026-09-24)**:
  - agrega configuración y un segundo modelo de spans;
  - en Python es menos maduro que el SDK de Sentry;
  - con el SDK de Sentry, la app y el backend se configuran y consultan igual antes de la presentación.
  
  Es una **desviación registrada** (Complexity Tracking). Los spans y la auditoría pasan por los hooks de `observability/hooks.py`, así que adoptar OTel después es registrar otro hook o cambiar el sink, sin tocar los servicios.

## §2 Dónde y cómo se inicializa (serverless, FR-009)

- **Decisión**: `configure_observability(settings)` en `adapters/observability/sentry_setup.py`, llamado desde `create_app()` **antes** de construir `FastAPI(...)`. Es idempotente: si el cliente ya está activo o no hay `SENTRY_DSN`, no hace nada. Registra los hooks de span y auditoría una sola vez (bandera de módulo), aunque `create_app` se llame varias veces (pruebas, recarga).
- **Razón**: la integración de FastAPI debe estar activa antes de instanciar la app. En Vercel el módulo se importa una vez por instancia; las invocaciones "warm" no vuelven a inicializar. Sin DSN (local, CI, pruebas) el backend se comporta exactamente igual que hoy (FR-004).
- **Alternativas**: inicializar en `api/index.py`. Descartado: las pruebas y `uvicorn` local no pasan por ese archivo.

## §3 Envío en Vercel sin perder telemetría ni bloquear (FR-007)

- **Decisión**: un middleware ASGI, al terminar cada respuesta, llama a `vercel.functions.wait_until(asyncio.to_thread(sentry_sdk.flush, 2.0))`. El paquete `vercel` (0.11.3) ya es dependencia del proyecto. Fuera de Vercel, `wait_until` no hace nada y el hilo de fondo del SDK envía de forma normal.
- **Razón**: el SDK envía en un hilo de fondo; en serverless, el proceso puede congelarse justo después de responder y perder lo que quedó en cola. `wait_until` mantiene viva la invocación **después** de enviar la respuesta, así que el pasajero no espera. El tiempo extra queda acotado por `maxDuration` (30 s).
- **Alternativas**: `sentry_sdk.flush()` síncrono al final de cada request. Descartado: suma hasta 2 s a la respuesta. Confiar solo en el hilo de fondo: se perderían eventos en instancias que se congelan (SC-001 exige verlos en menos de 1 minuto). **Verificación obligatoria**: quickstart §3 confirma en un deploy real que un error aparece en Sentry.

## §4 Privacidad (FR-006, SC-003)

- **Decisión**: dos capas.
  1. **Configuración del SDK**: `send_default_pii=False`, `include_local_variables=False`, `max_request_body_size="never"`.
  2. **Un único `SentryPrivacyFilter`** conectado a `before_send`, `before_send_transaction`, `before_breadcrumb`, `before_send_log` y `before_send_metric`. Borra `user`, cookies y cabeceras (salvo `content-type` y `user-agent`), el cuerpo del request y los query strings. Reemplaza el mensaje de excepciones de librerías de terceros por `[redactado]` y conserva tipo y pila. Solo deja pasar logs y métricas de la lista blanca de [contracts/telemetry-events.md](contracts/telemetry-events.md).
  Tercera capa en Sentry: "Prevent Storing of IP Addresses" y Data Scrubbing por defecto del proyecto `aeropass-back`.
- **Razón**: los riesgos concretos en este código son:
  - `include_local_variables` (activo por defecto) enviaría variables de cada frame: `datos`, `numero_documento`, bytes de imagen, el token firmado y hasta la clave privada.
  - El cuerpo JSON de `POST /v1/identity` lleva nombre y número de documento, y el SDK lo adjuntaría con el tamaño `medium` por defecto.
  - Los mensajes de asyncpg repiten valores (`Key (numero)=(…) already exists`).
  - httpx registra en `INFO` cada URL completa.
  Los mensajes de las excepciones propias (`DomainError`) son textos fijos en inglés, sin datos, así que se conservan.
- **Alternativas**: depender solo del Data Scrubbing de Sentry. Descartado: trabaja por nombres de campo y no reconoce un número de documento dentro de un mensaje o una variable local. Lista negra de campos: descartada, porque una lista blanca falla cerrada.

## §5 Señales por métrica

| Métrica | Señal | Por qué |
|---|---|---|
| Excepciones no controladas | Eventos de error de la integración FastAPI (`error.unhandled:true`) | Nativo; los `DomainError` (4xx esperados) los atiende el handler y no son eventos. |
| Autoservicio (KR A1.2) | Métrica `aeropass.pasajero.estado_final` (count, atributo `aeropass.estado`) | Cada pasajero llega a un estado final **una sola vez** (la transición exige `PENDIENTE_VERIFICACION`), así que contar eventos = contar pasajeros, sin `count_unique`. Las métricas no se muestrean. |
| Auto rechazo | Métrica `aeropass.verificacion.intento` (count, atributos `aeropass.resultado`, `aeropass.motivo`) | Un evento por intento; `NO_CONCLUYENTE` queda como valor propio para excluirlo. |
| Disponibilidad (KR A2.1) | Monitor de Uptime de Sentry sobre `GET /health`, cada 1 minuto | Clarificación del spec. Es externo: detecta caídas aunque no haya tráfico ni pueda correr el código. |
| Latencia de pase | Transacciones `POST /v1/passes` y `GET /v1/passes/{credencial_id}`, `p95`/`p99(span.duration)` | Nativo de la integración; con muestreo (§8). |
| Contingencia (KR A2.5) | Métrica `aeropass.circuit_breaker.apertura` (count, atributo `aeropass.dependencia`) + log | Una apertura ya es un pico (5 fallos en 60 s); el atributo identifica la dependencia (FR-014). |

Además, cada evento de auditoría se envía como **log** de Sentry (`aeropass.event`, resultado y atributos de la lista blanca) para correlación y diagnóstico (FR-003). Las métricas alimentan dashboard y alertas.

- **Alternativas**: solo logs, como en la app. Descartado: los logs no admiten agregados condicionales, así que ni la proporción ni su alerta caben en una consulta (limitación encontrada en 015). En la app hacía falta `count_unique`; aquí no, porque cada transición ocurre una sola vez.

## §6 Cómo llega el resultado de la verificación a la auditoría (FR-003, FR-010, FR-011)

- **Decisión**: `@audited` acepta un parámetro opcional `describe: Callable[[R], Mapping[str, str | None]]` que, **solo en éxito**, agrega atributos al evento a partir del valor retornado. El facade lo usa para declarar `resultado`, `motivo` y `estado_final`. El dominio expone `Pasajero.estado_final` (el estado si es `VERIFICADO` o `REQUIERE_REVISION_MANUAL`, si no `None`), fuente única de qué estados son finales (Principio IV).
- **Razón**: el evento sale **después** del `commit` (el decorador envuelve todo el método), así que no se cuenta un intento que luego se revierte. La lógica del facade no cambia: la anotación es declarativa (Decorator, FR-002). Una métrica nueva sobre un paso ya auditado solo necesita `describe` o una entrada en el mapeo del sink (SC-006).
- **Alternativas**: `emit_audit(...)` dentro de `BiometricVerificationService.record_attempt`. Descartado: se emite antes del `commit`. Un tercer tipo de hook, "métrica": descartado, porque duplica el mecanismo (FR-010 pide reutilizar los mismos puntos).

## §7 Circuit breaker observable (FR-008, FR-014, US2-2)

- **Decisión**: `CircuitBreaker` emite `emit_audit("resilience.circuit_opened", dependencia=<nombre>)` en **ambas** aperturas (umbral de fallos en `CLOSED` y fallo de la prueba en `HALF_OPEN`). Cada `call` corre dentro de un span `circuit_breaker.<nombre>`, abierto con el nuevo context manager público `hooks.span(name)`. El span hook de Sentry traduce la excepción a estado: `TimeoutError` → `deadline_exceeded`, `CircuitOpenError` → `unavailable`, `CancelledError` → `cancelled`, otra → `internal_error`.
- **Razón**: hoy la apertura solo se escribe en `logger.warning`, y la reapertura desde `HALF_OPEN` no deja rastro. El estado del span distingue timeout, circuito abierto y error de código sin logs extra (US2-2).
- **Alternativas**: detectar la apertura leyendo el log `"circuit breaker opened"`. Descartado: frágil y no cubre `HALF_OPEN`.

## §8 Muestreo y costo

- **Decisión**: `SENTRY_TRACES_SAMPLE_RATE` (por defecto 1.0; en prod se fija 0.2 en Vercel) con un `traces_sampler` que devuelve 0 para `/health`. Errores, logs de la lista blanca y métricas: 100%. Sin profiling.
- **Razón**: el monitor de salud genera 1.440 requests al día; trazarlos sería el mayor costo y no aporta. Con volumen de piloto, el p95/p99 de pase al 20% sigue siendo representativo; si en el piloto hay menos de ~100 emisiones al día, se sube la tasa en Vercel sin desplegar código.
- **Alternativas**: 100% de trazas en prod. Descartado por la recomendación del spec (margen por validación).

## §9 Endpoint de salud (FR-013a)

- **Decisión**: `GET /health`, sin autenticación. Responde `200 {"estado":"ok"}` o `503 {"estado":"no_disponible"}` con `Cache-Control: no-store`. Contrato en [contracts/health-endpoint.md](contracts/health-endpoint.md). `HealthService` recibe una lista de `HealthCheck` (puerto) y los corre en paralelo, con un timeout de 3 s cada uno: `DatabaseHealthCheck` (`SELECT 1` con el pool existente) y `RedisHealthCheck` (`PING` con el singleton). En modo `fake` no hay Redis real, así que solo se verifica la base de datos.
- **Razón**: SOLID (Principio III): los checks son intercambiables y se prueban con dobles. No revela detalles internos. Como efecto secundario, la consulta cada minuto mantiene una instancia caliente.
- **Alternativas**: verificar también Blob y QStash. Descartado: el spec fija base de datos y Redis; los proveedores inestables ya se miden por sus circuit breakers.

## §10 Entornos y release (FR-005)

- **Decisión**: `SENTRY_ENVIRONMENT` ∈ `prod`, `demo`, `dev` (por defecto `dev`), y `simulated` para datos de validación. Son los mismos nombres de la app, así que los dashboards de los dos proyectos usan el mismo filtro. `release` = `VERCEL_GIT_COMMIT_SHA` si existe.
- **Razón**: el spec dice development/staging/production. Se interpreta como dev, demo y prod por consistencia con la app, sin cambiar la intención del requisito.

## §11 Alertas y la regla de volumen mínimo (FR-017)

- **Decisión**: igual que la app (desviación **D1**). Los monitores de métricas de Sentry no pueden exigir "al menos 10 pasajeros en estado final", así que la alerta de autoservicio evalúa la hora completa y el correo sugiere revisar el volumen en el dashboard. Detalle en [contracts/dashboard-and-alerts.md](contracts/dashboard-and-alerts.md).
- **Razón**: la clarificación pidió "las mismas reglas que la app"; el usuario aceptó D1 para la app el 2026-09-23 y para el backend el 2026-09-24. FR-017 y la Historia 7 ya están ajustados.
- **Alternativas**: un evaluador programado que consulte la API de Sentry. Descartado: es código nuevo y contradice la configuración manual (FR-018).

## §12 Preparación de la demo (FR-019, Historia 9)

- **Decisión**: herramienta `aeropass.tools.seed_demo`. Construye la app en proceso con adaptadores `fake` (sin Clerk ni Blob reales), Postgres real (`DATABASE_URL` de una rama de Neon que no sea prod) y el DSN real con `SENTRY_ENVIRONMENT=demo` (o `simulated`). Usa los marcadores del mock biométrico:
  - pasajeros con `ok` → `VERIFICADO`;
  - pasajeros con tres `spoof`/`other` → `REQUIERE_REVISION_MANUAL`;
  - intentos `timeout` sueltos → `NO_CONCLUYENTE`;
  - emisiones y consultas de pase para la latencia.
  Opciones: `--contingencia` (5 timeouts seguidos que abren el breaker) y `--solo-error` (solo lanza una excepción no controlada en una ruta que existe únicamente en la app de la herramienta). Así la demo prueba en vivo las alertas inmediatas sin agregar un endpoint de depuración al código desplegable.
- **Razón**: no depende de Clerk ni de un deploy de demo, y el camino de telemetría es el mismo que en producción.
- **Alternativas**: `tools/e2e_flow.py` contra un deploy de preview. Descartado como camino principal: exige Clerk y un deploy con `SENTRY_ENVIRONMENT=demo`. Sigue sirviendo para validar prod.
- **Nota**: `--contingencia` y `--solo-error` son la preparación de la demo pedida por el spec, no el trabajo de inyección de fallos diferido.

## §13 Catálogo de fallos (anexo C): qué señal verifica cada hipótesis

Fuente: recorrido del pasajero que compartió el equipo (anexo C del documento final, fallos F1–F18 y A10). **La inyección de fallos sigue diferida.** Esta sección solo asegura que, cuando se haga, la observabilidad ya tenga la señal que confirma o refuta cada hipótesis que toca este backend.

| Fallo | KR | Hipótesis | Señal que la verifica | Widget / alerta |
|---|---|---|---|---|
| F3: proveedor de comparación facial no disponible | A2.3 | Los casos se escalan sin detener la fila | `aeropass.circuit_breaker.apertura{dependencia=biometric}`; `aeropass.verificacion.intento{resultado=NO_CONCLUYENTE}` | W7, W8, B3 |
| F4: comparación facial con latencia elevada | A2.4 | Se aplica el timeout, se escala y no se agota el pool | Span `circuit_breaker.biometric` con estado `deadline_exceeded`; p95 del paso | W9 |
| F18: cuota del servicio de vivacidad agotada (429) | A2.3 | La limitación se detecta y no se rechazan pasajeros legítimos | Span HTTP de httpx hacia el proveedor con `http.response.status_code=429` (el adaptador lo trata como `ProviderUnavailable` → `NO_CONCLUYENTE`, nunca como `FALLIDO`) | W10, W8 |
| F7: conmutación de la base de datos | A2.1 | Reconexión automática sin perder validaciones | `GET /health` → 503 durante el corte; monitor de Uptime; errores no controlados en la ventana | W5, W1, B4 |
| F8: caché de control de reuso no disponible | A2.2 | Degrada sin abrir una ventana de reuso | `/health` (check `redis`); log `credential.issue` con `aeropass.error_type=AlmacenamientoNoDisponible` (el servicio revoca la credencial si no pudo registrar el token) | W5, W11 |
| F11: servicio de firma no disponible | A2.1 | No se emiten credenciales nuevas, las vigentes siguen operando | Log `credential.issue` con `outcome=error`; error no controlado en `POST /v1/passes` si falta la clave | W11, W1, B1 |

Notas del recorrido que afectan estas hipótesis:
- **F7**: Neon no ofrece conmutación entre zonas como RDS Multi-AZ; la hipótesis debe reescribirse antes de ejecutar el experimento.
- **F3/F4/F18**: exigen que el cliente apunte a un proxy (Toxiproxy). Ya es posible: `VISION_PROVIDER_URL` es una variable de entorno.

Fuera del alcance de este repo (checkpoint, lector, aerolínea, consola): F1, F2, F5, F6, F9, F10, F12, F14, F15, F16 y A10.

## §14 KR A2.7 (registro inmutable del 100% de las validaciones): fuera de alcance

- **Decisión**: los logs de auditoría enviados a Sentry **no** se presentan como cumplimiento de A2.7.
- **Razón**: Sentry tiene retención limitada según el plan y no ofrece almacenamiento que impida editar o borrar. Además, las validaciones del checkpoint no están en este repo (Principio II). El recorrido del equipo señala que el Servicio de Auditoría desapareció entre la arquitectura de referencia y la de implementación, y que en Vercel ni siquiera Blob ofrece bloqueo de objetos.
- **Alternativas**: usar Sentry como registro de auditoría. Descartado: no garantiza que el registro sea inmutable ni que esté completo. Queda como deuda del documento, no de esta feature.

## §15 Línea base del repositorio (2026-09-24)

- Con Python 3.11 en un entorno aparte: **95 pruebas unitarias y de contrato pasan**. **68 pruebas de integración dan error en esta máquina** porque el Postgres embebido (`pgserver`) no arranca sobre el `.pgdata/` versionado. Se corren con `TEST_DATABASE_URL` (Postgres local o rama de Neon).
- `.pgdata/` y varios `__pycache__/` están versionados en git; correr la suite los modifica. Es un problema aparte de esta feature.
- El Python por defecto de la máquina es 3.14, para el que `pgserver` no tiene wheels: hay que usar `uv run --python 3.11`.

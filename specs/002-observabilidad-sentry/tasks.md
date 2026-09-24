---
description: "Lista de tareas de la feature 002: observabilidad con Sentry (backend)"
---

# Tasks: Observabilidad con Sentry (Backend)

**Input**: `specs/002-observabilidad-sentry/` (plan.md, spec.md, research.md, data-model.md, contracts/, quickstart.md)

**Constitución**: 1.1.0 (Principios I, II y VI enmendados el 2026-09-24)

**Tests**: se incluyen. La frontera de privacidad es test-first: las pruebas del filtro, de la lista blanca y de la configuración del SDK se escriben y **fallan** antes del código (plan, Constitution Check; contracts/privacy-filter.md).

**Organización**: por historia de usuario del spec. La Historia 8 está diferida y no tiene tareas. La inyección de fallos está diferida: `seed_demo --contingencia` es preparación de la demo (research §12), no el trabajo del anexo C.

## Formato: `[ID] [P?] [Story] Descripción`

- **[P]**: se puede hacer en paralelo (otro archivo, sin dependencias pendientes).
- **[USn]**: historia de usuario del spec.
- **(manual)**: se hace en la UI de Sentry o Vercel, no en el código; su resultado se registra en el archivo indicado.

## Convenciones

- Código en `src/aeropass/`, pruebas en `tests/`.
- Correr las pruebas con `uv run --python 3.11 pytest` (el 3.14 por defecto no tiene wheels de `pgserver`).
- Las pruebas de integración necesitan `TEST_DATABASE_URL`. El Postgres embebido no arranca sobre el `.pgdata/` versionado, pero sí sobre un directorio limpio fuera del repo (`pgserver.get_server(<dir limpio>)`), que se usa como `TEST_DATABASE_URL` (research §15). Así la suite completa corre sin tocar `.pgdata/`. Si se usa el `.pgdata/` del repo, restaurarlo después con `git checkout -- .pgdata`.
- Ninguna prueba usa red ni un DSN real: usan el transporte en memoria de T006.

---

## Phase 1: Setup (infraestructura compartida)

**Propósito**: dependencia, configuración y variables.

- [X] T001 Agregar `sentry-sdk[fastapi]>=2.70,<3` a `dependencies` en `pyproject.toml` y regenerar `uv.lock` con `uv lock --python 3.11`
- [X] T002 [P] Agregar a `src/aeropass/config.py` los campos `sentry_dsn: str = ""`, `sentry_environment: str = "dev"`, `sentry_traces_sample_rate: float = 1.0` (validador: rango [0, 1]) y `vercel_git_commit_sha: str = ""` (la variable que inyecta Vercel), según data-model.md §6
- [X] T003 [P] Agregar a `.env.example` una sección "Sentry" con `SENTRY_DSN=`, `SENTRY_ENVIRONMENT=dev` (valores: `prod`, `demo`, `dev`, `simulated`) y `SENTRY_TRACES_SAMPLE_RATE=1.0` (0.2 en prod), con comentarios en el estilo del archivo

---

## Phase 2: Foundational (prerrequisitos bloqueantes)

**Propósito**: catálogo, filtro de privacidad, hooks extendidos, sinks e inicialización. Toda historia depende de esta fase.

**⚠️ CRÍTICO**: ninguna historia empieza hasta terminar esta fase. Las pruebas T006–T010 se escriben primero y deben fallar.

### Catálogo y apoyo de pruebas

- [X] T004 Crear `src/aeropass/observability/telemetry_catalog.py` como fuente única (Principio IV), sin importar `sentry_sdk`:
  - nombres de eventos de auditoría: `identity.verification`, `credential.issue`, `credential.consume`, `auth.authenticate`, `resilience.circuit_opened`;
  - política de log por evento (`auth.authenticate`: solo `error`; `resilience.circuit_opened`: nivel `warning`);
  - atributos de log permitidos (`aeropass.event`, `aeropass.outcome`, `aeropass.error_type`, `aeropass.resultado`, `aeropass.motivo`, `aeropass.estado`, `aeropass.dependencia`);
  - un registro vacío de reglas de métrica (nombre, evento de origen, atributos permitidos y función que decide si se emite), para que cada historia agregue la suya.
  Ver contracts/telemetry-events.md.
- [X] T005 Reemplazar los nombres de evento escritos como texto en los `@audited` existentes por las constantes del catálogo (T004), para que cada nombre exista en un solo lugar (Principio IV). Archivos: `src/aeropass/adapters/auth/clerk.py`, `src/aeropass/adapters/fakes/auth.py`, `src/aeropass/services/credential_lifecycle_service.py`, `src/aeropass/services/pass_issuance_service.py` y `src/aeropass/services/identity_verification_facade.py`. Criterio: `grep -rn '@audited("' src/` no devuelve resultados.
- [X] T006 [P] Crear `tests/support/sentry_capture.py` (y `tests/support/__init__.py`) con un fixture `sentry_capture`:
  - inicializa `sentry_sdk` con un transporte en memoria que guarda los envelopes (errores, transacciones, logs, métricas) y las opciones reales de `configure_observability`;
  - al terminar, cierra el cliente y limpia los hooks registrados (`hooks.clear_hooks()`).
  Registrarlo en `tests/conftest.py`.

### Pruebas primero (deben fallar)

- [X] T007 [P] Escribir `tests/unit/test_sentry_privacy.py` con **todos** los casos obligatorios de contracts/privacy-filter.md:
  - `request.data` con `numero_documento` → sale sin `data`;
  - `UniqueViolationError` de asyncpg → `value = "[redactado]"` y conserva el tipo;
  - una `DomainError` conserva su mensaje;
  - una URL con `?token=x` pierde la query en el evento, el breadcrumb y el span;
  - un log con un atributo desconocido lo pierde; un log de `httpx` se descarta;
  - una métrica fuera del catálogo se descarta;
  - la cabecera `authorization` nunca sale;
  - `before_send` nunca devuelve `None` para un error.
- [X] T008 [P] Escribir `tests/contract/test_telemetry_allowlist.py`. Para cada evento del catálogo, emitir con `emit_audit` todas las combinaciones de atributos que produce su `describe` y comprobar dos cosas: cada atributo de log y de métrica capturado está en la lista blanca de contracts/telemetry-events.md, y ninguna métrica fuera del catálogo llega al transporte.
- [X] T009 [P] Escribir `tests/unit/test_observability_setup.py`:
  - sin `SENTRY_DSN`, `configure_observability` no inicializa el cliente ni registra hooks, y `create_app()` responde igual;
  - con DSN, las opciones son `send_default_pii=False`, `include_local_variables=False`, `max_request_body_size="never"`, `enable_logs=True`, los cinco hooks del filtro, el `environment` y el `release`;
  - llamar dos veces a `create_app` registra los hooks una sola vez (FR-009);
  - `traces_sampler` devuelve 0 para `/health` y la tasa configurada para el resto.
- [X] T010 [P] Escribir `tests/unit/test_hooks.py`:
  - `span(name)` abre y cierra los span hooks registrados, y relanza la excepción sin cambiarla;
  - `@audited(event, describe=fn)` agrega los atributos de `fn(resultado)` solo en éxito, omite los valores `None`, y en error emite `outcome=error` con `error=<tipo>` sin llamar a `describe`;
  - sin `describe`, el comportamiento actual no cambia (sync y async).
- [X] T011 [P] Escribir `tests/unit/test_sentry_sinks.py`:
  - `SentrySpanHook` crea un span `op="aeropass.step"` con el nombre recibido y mapea `TimeoutError`→`deadline_exceeded`, `CircuitOpenError`→`unavailable`, `CancelledError`→`cancelled` y otra excepción→`internal_error`;
  - `SentryAuditSink` produce un log `aeropass.audit` por evento con los atributos del catálogo, aplica la política por evento (no hay log de `auth.authenticate` ok) y emite las métricas que definan las reglas del catálogo.

### Implementación

- [X] T012 Implementar `src/aeropass/adapters/observability/sentry_privacy.py` (`SentryPrivacyFilter` con `before_send`, `before_send_transaction`, `before_breadcrumb`, `before_send_log` y `before_send_metric`, más `adapters/observability/__init__.py`) según contracts/privacy-filter.md. Las listas blancas se leen del catálogo (T004). Hace pasar T007.
- [X] T013 Extender `src/aeropass/observability/hooks.py`:
  - convertir `_spans` en `span(name)` público y mantener el alias interno;
  - agregar el parámetro opcional `describe: Callable[[R], Mapping[str, str | None]] | None = None` a `audited`, que agrega atributos solo en éxito (research §6).
  Hace pasar T010.
- [X] T014 Implementar `src/aeropass/adapters/observability/sentry_sinks.py`: `SentrySpanHook` (context manager con `sentry_sdk.start_span`, estado según la excepción) y `SentryAuditSink` (log vía `sentry_sdk.logger`, métricas vía `sentry_sdk.metrics.count` según las reglas del catálogo). Hace pasar T011 y T008.
- [X] T015 Implementar `src/aeropass/adapters/observability/sentry_setup.py`:
  - `configure_observability(settings)`: idempotente con una bandera de módulo; no hace nada sin DSN;
  - integraciones: FastAPI/Starlette con `transaction_style="url"`, SQLAlchemy, httpx y `LoggingIntegration` (logs de Sentry a partir de `WARNING` y solo para loggers `aeropass.*`, filtrados por el hook `before_send_log`);
  - `traces_sampler`: 0 para `/health`;
  - registra una sola vez `SentrySpanHook` y `SentryAuditSink` con `hooks.register_*`;
  - `FlushTelemetryMiddleware`: envoltorio ASGI que, al terminar cada request HTTP, llama a `vercel.functions.wait_until(asyncio.to_thread(sentry_sdk.flush, 2.0))` solo si el cliente está activo (research §3).
  Hace pasar T009.
- [X] T016 Conectar en `src/aeropass/main.py`: resolver el `container` y llamar a `configure_observability(container.settings)` **antes** de `FastAPI(...)`. En `api/index.py`, envolver la app en `FlushTelemetryMiddleware`. **No** usar `app.add_middleware`: Sentry captura los errores en su envoltorio ASGI, que queda afuera de todo middleware de la app (research §3). Correr la suite completa sin DSN: sin regresiones (quickstart §1).

**Checkpoint**: filtro, hooks, sinks e inicialización en verde; sin DSN el backend se comporta igual que antes.

---

## Phase 3: User Story 1 — Captura automática de excepciones no controladas (P1) 🎯 MVP

**Goal**: toda excepción no controlada queda en Sentry con ruta, método y pila, sin datos sensibles y sin alterar la respuesta.

**Independent Test**: con el transporte en memoria, una ruta que lanza `RuntimeError` produce un evento con la ruta como plantilla, el método y la pila, sin cuerpo ni variables locales. La respuesta es la misma con y sin DSN.

### Tests (primero)

- [X] T017 [P] [US1] Escribir `tests/integration/test_unhandled_errors.py`. Con `create_app` y una ruta de prueba agregada solo en la prueba, que recibe un JSON con `numero_documento` y lanza `RuntimeError`:
  - se captura un evento con `transaction` = plantilla de la ruta, `request.method`, pila completa, `error.unhandled`, sin `request.data` ni `vars` en los frames;
  - la respuesta HTTP es 500, igual que con el mismo test sin DSN;
  - un `DomainError` (p. ej. `DatosInvalidos`) y un `RequestValidationError` **no** generan eventos.
- [X] T018 [P] [US1] Agregar a `tests/integration/test_unhandled_errors.py` un caso de logging: un `logger.exception` de un logger `aeropass.*` (como el del despachador del outbox) genera un evento de error, y un `logger.info` de `httpx` no genera ni evento ni log.

### Implementación

- [X] T019 [US1] Ajustar la configuración de la integración FastAPI en `src/aeropass/adapters/observability/sentry_setup.py` (`failed_request_status_codes` de 500 a 599 y, si hace falta, `LoggingIntegration(event_level=logging.ERROR)`) hasta que T017 y T018 pasen, sin tocar los handlers de `main.py`. *Resultado: no hizo falta ningún cambio, porque la configuración de T015 ya los cubría. Una prueba de mutación (variables locales, cuerpo de request y `before_send` desactivados) hace fallar T017, así que las pruebas sí protegen.*

**Checkpoint**: MVP. Con DSN, las fallas de código llegan a Sentry sin datos sensibles.

---

## Phase 4: User Story 6 — Disponibilidad, latencia y contingencia (P1)

**Goal**: `GET /health` para el monitor de Uptime, latencia de pase medible y una apertura de circuit breaker visible por dependencia.

**Independent Test**: `/health` responde 200 con los checks bien y 503 sin detalles cuando uno falla o se cuelga. Una apertura del breaker `biometric` produce la métrica `aeropass.circuit_breaker.apertura{aeropass.dependencia=biometric}`. `POST /v1/passes` produce una transacción con ese nombre.

### Tests (primero)

- [X] T020 [P] [US6] Escribir `tests/contract/test_health_contract.py` según contracts/health-endpoint.md:
  - 200 `{"estado":"ok"}` con checks que terminan bien;
  - 503 `{"estado":"no_disponible"}` con un check que lanza y con uno que se cuelga (timeout corto inyectado);
  - `Cache-Control: no-store`;
  - el cuerpo nunca contiene el nombre del check ni el mensaje;
  - sin autenticación; fuera del esquema OpenAPI.
- [X] T021 [P] [US6] Escribir `tests/unit/test_health_service.py`: los checks corren en paralelo (dos checks de 0,2 s tardan menos de 0,4 s en total); el timeout acota el total; el reporte interno lista los checks fallidos.
- [X] T022 [P] [US6] Extender `tests/unit/test_circuit_breaker.py` con un hook de auditoría de prueba:
  - al llegar al umbral en `CLOSED` se emite `resilience.circuit_opened` con `dependencia=<nombre>`;
  - al fallar la prueba en `HALF_OPEN` se emite otra vez;
  - un fallo que no abre el circuito no emite nada;
  - cada `call` corre dentro de `span("circuit_breaker.<nombre>")`, también cuando se rechaza con `CircuitOpenError`.
- [X] T023 [P] [US6] Escribir `tests/integration/test_pass_latency_telemetry.py`: con el transporte en memoria, `POST /v1/passes` y `GET /v1/passes/{id}` producen transacciones con los nombres `/v1/passes` y `/v1/passes/{credencial_id}` (plantilla de la ruta, sin el método ni el UUID), y `GET /health` no produce transacción.

### Implementación

- [X] T024 [P] [US6] Crear el puerto `src/aeropass/ports/health.py`: `HealthCheck` (`Protocol` con `name: str` y `async check() -> None`) y `HealthReport` (`ok: bool`, `fallidos: tuple[str, ...]`), según data-model.md §5.
- [X] T025 [P] [US6] Crear `src/aeropass/adapters/health/checks.py` (y su `__init__.py`): `DatabaseHealthCheck`, que corre `SELECT 1` con el `async_sessionmaker`, y `RedisHealthCheck`, que corre `PING` con el cliente de `get_redis()`.
- [X] T026 [US6] Crear `src/aeropass/services/health_service.py`: `HealthService(checks, timeout=3.0)` corre los checks con `asyncio.gather` y `asyncio.wait_for`, y escribe los fallidos en el logger `aeropass.health` a nivel `WARNING`, sin detalles de conexión. Hace pasar T021.
- [X] T027 [US6] Agregar `health_service` al `Container` en `src/aeropass/api/deps.py`, con `DatabaseHealthCheck` siempre y `RedisHealthCheck` solo si no es modo `fake`, más la dependencia `get_health_service`.
- [X] T028 [US6] Crear `src/aeropass/api/routers/health.py` (`GET /health`, `include_in_schema=False`, 200/503 y `Cache-Control: no-store`) e incluirlo en `_include_routers` de `src/aeropass/main.py`. Hace pasar T020.
- [X] T029 [US6] Modificar `src/aeropass/adapters/resilience/circuit_breaker.py`: envolver `call` en `hooks.span(f"circuit_breaker.{self.name}")` y emitir `emit_audit("resilience.circuit_opened", dependencia=self.name)` en las dos aperturas de `_record_failure`, conservando el `logger.warning`. Hace pasar T022.
- [X] T030 [US6] Agregar al catálogo (`src/aeropass/observability/telemetry_catalog.py`) la regla de métrica `aeropass.circuit_breaker.apertura` (evento `resilience.circuit_opened`, atributo `aeropass.dependencia`) y verificar T008 y T011.

**Checkpoint**: salud, latencia y contingencia medibles.

---

## Phase 5: User Story 2 — Trazas del pipeline crítico (P2)

**Goal**: un span por paso `@traced` y por llamada externa; los timeouts y los circuitos abiertos se distinguen por estado.

**Independent Test**: un flujo de registro → verificación → pase con adaptadores falsos produce una transacción con los spans `aeropass.step` esperados. Un selfie `MOCK:timeout` deja un span `circuit_breaker.biometric` con estado `deadline_exceeded`.

### Tests (primero)

- [X] T031 [P] [US2] Escribir `tests/integration/test_pipeline_traces.py`:
  - el flujo completo con los helpers de `tests/integration/helpers.py` produce spans `aeropass.step` para `registration.register`, `facade.verify_and_create_identity`, `verification.record_attempt`, `identity.create_for_success`, `biometrics.mock.evaluate`, `circuit_breaker.biometric` y `passes.issue`;
  - con `MOCK:timeout`, el span `circuit_breaker.biometric` tiene estado `deadline_exceeded`;
  - con el breaker abierto, tiene estado `unavailable`;
  - ningún span de SQL o HTTP lleva valores de parámetros ni query strings.

### Implementación

- [X] T032 [US2] Corregir lo que T031 revele en `src/aeropass/adapters/observability/sentry_sinks.py` o `sentry_privacy.py` (p. ej. que el span del paso quede como hijo de la transacción de la ruta, o que haya datos de span sin filtrar), sin tocar servicios ni adaptadores de negocio. *Resultado: no hizo falta ningún cambio. Si el sink reporta todo como `internal_error` (prueba de mutación), fallan las pruebas de timeout y de circuito abierto.*

**Checkpoint**: trazas completas del pipeline.

---

## Phase 6: User Story 3 — Auditoría correlacionable sin datos sensibles (P3)

**Goal**: cada `@audited` queda como log correlacionable con su resultado, sin datos del payload.

**Independent Test**: emitir un pase (ok) y un pase fallido producen logs `aeropass.audit` con `aeropass.event=credential.issue` y `aeropass.outcome` `ok`/`error`. El de error lleva `aeropass.error_type` y ningún mensaje.

### Tests (primero)

- [X] T033 [P] [US3] Escribir `tests/integration/test_audit_logs.py`:
  - `POST /v1/passes` exitoso → log `credential.issue` `ok`;
  - `POST /v1/passes` sin identidad activa → log `credential.issue` `error` con `aeropass.error_type=IdentidadNoActiva` y sin mensaje;
  - token inválido → log `auth.authenticate` `error`;
  - un request autenticado correctamente no produce log `auth.authenticate`;
  - ningún log contiene número o nombre de documento, `MOCK:` ni el token del pase.

### Implementación

- [X] T034 [US3] Ajustar `SentryAuditSink` (`src/aeropass/adapters/observability/sentry_sinks.py`) o la política del catálogo hasta que T033 pase. Si algún `@audited` emite atributos extra, deben pasar por la lista blanca. *Resultado: no hizo falta ningún cambio. Si la autenticación exitosa también se registrara (prueba de mutación), falla la prueba de "solo errores de autenticación".*

**Checkpoint**: la auditoría es consultable en Sentry.

---

## Phase 7: User Story 4 — Tasa de autoservicio, KR A1.2 (P2)

**Goal**: contar a los pasajeros que llegan a `VERIFICADO` o `REQUIERE_REVISION_MANUAL`, una vez cada uno y después del `commit`.

**Independent Test**: un pasajero con `ok` produce `aeropass.pasajero.estado_final{estado=VERIFICADO}` una vez. Uno con tres `spoof` produce `{estado=REQUIERE_REVISION_MANUAL}` una sola vez, al tercer intento. Uno con `timeout` no produce la métrica.

### Tests (primero)

- [X] T035 [P] [US4] Agregar a `tests/unit/test_passenger.py` pruebas de `Pasajero.estado_final`: `None` en `PENDIENTE_VERIFICACION`, el estado en `VERIFICADO` y en `REQUIERE_REVISION_MANUAL`.
- [X] T036 [P] [US4] Escribir `tests/integration/test_verification_telemetry.py`, parte autoservicio: casos del Independent Test. Además, si el `commit` falla (UoW de prueba que lanza en `commit`), no se emite ninguna métrica.

### Implementación

- [X] T037 [US4] Agregar la propiedad `estado_final -> EstadoPasajero | None` a `Pasajero` en `src/aeropass/domain/passenger.py`. Hace pasar T035.
- [X] T038 [US4] Anotar `verify_and_create_identity` en `src/aeropass/services/identity_verification_facade.py` con `@audited("identity.verification", describe=...)`. `describe` devuelve `resultado`, `motivo` y `estado` a partir de `VerificationOutcome`, con valores de enum como texto y `None` cuando no aplican (data-model.md §2). No cambia ninguna otra línea del método.
- [X] T039 [US4] Agregar al catálogo la regla `aeropass.pasajero.estado_final` (evento `identity.verification` ok con `estado` presente, atributo `aeropass.estado`). Hace pasar T036 y verifica T008.

**Checkpoint**: autoservicio medible sin consultar la base de datos.

---

## Phase 8: User Story 5 — Tasa de auto rechazo (P2)

**Goal**: contar los intentos por resultado y motivo; los `NO_CONCLUYENTE` quedan aparte.

**Independent Test**: `ok` → `resultado=EXITOSO`; `spoof` → `FALLIDO/LIVENESS`; `other` → `FALLIDO/COMPARACION`; `timeout` → `NO_CONCLUYENTE` sin motivo. Un error no controlado no genera la métrica.

### Tests (primero)

- [X] T040 [P] [US5] Agregar a `tests/integration/test_verification_telemetry.py` los casos del Independent Test para `aeropass.verificacion.intento`, incluido que el 429 del proveedor (simulado con un `VisionProviderAdapter` sobre `httpx.MockTransport`) termina en `NO_CONCLUYENTE` y nunca en `FALLIDO` (hipótesis de F18, research §13).

### Implementación

- [X] T041 [US5] Agregar al catálogo la regla `aeropass.verificacion.intento` (evento `identity.verification` ok, atributos `aeropass.resultado` y `aeropass.motivo`). Hace pasar T040. No debe requerir cambios en `hooks.py` ni en el facade (SC-006).

**Checkpoint**: auto rechazo separado de los errores técnicos.

---

## Phase 9: User Story 9 — Demostración en 15 minutos (P1)

**Goal**: datos preparados en un entorno que no es prod y alertas inmediatas en vivo, sin endpoints de depuración en el código desplegable.

**Independent Test**: `seed_demo` con el transporte en memoria genera la mezcla esperada de métricas y logs. `--solo-error` produce exactamente un error no controlado. `--contingencia` produce una apertura de `biometric`.

- [ ] T042 [P] [US9] Escribir `tests/integration/test_seed_demo.py`: ejecutar la función principal de la herramienta con el transporte en memoria y la base de pruebas, y comprobar:
  - al menos 1 `estado_final=VERIFICADO` y 1 `REQUIERE_REVISION_MANUAL`;
  - intentos `FALLIDO` de ambos motivos y al menos 1 `NO_CONCLUYENTE`;
  - transacciones de pase;
  - `--contingencia` → métrica de apertura `biometric`;
  - `--solo-error` → un solo evento no controlado y ninguna métrica.
- [ ] T043 [US9] Implementar `src/aeropass/tools/seed_demo.py` según research §12:
  - app en proceso con adaptadores `fake`, `DATABASE_URL` de una base que no sea prod y `SENTRY_DSN`/`SENTRY_ENVIRONMENT` del entorno; se niega a correr con `SENTRY_ENVIRONMENT=prod`;
  - pasajeros `ok`, pasajeros con tres `spoof`/`other` e intentos `timeout`, emisiones y consultas de pase;
  - `--contingencia`: timeout biométrico corto y 5 `timeout` seguidos;
  - `--solo-error`: ruta que solo existe en la app de la herramienta;
  - imprime la tasa de autoservicio y de auto rechazo esperadas y hace `sentry_sdk.flush()` al terminar.
  Hace pasar T042.
- [ ] T044 [US9] Documentar en `README.md` (sección nueva "Observabilidad (Sentry)") el procedimiento de la demo: correr `seed_demo` al menos 1 hora antes, en `demo`; qué mostrar de cada widget; `--solo-error` en vivo para B1; y que B2 se muestra configurada, sin afirmar que disparó (Historia 9, escenario 3).

**Checkpoint**: la herramienta puebla cualquier entorno que no sea prod.

---

## Phase 10: User Story 7 — Panel y alertas en Sentry (P1)

**Goal**: dashboard de 11 widgets, 4 alertas por correo y monitor de Uptime en `aeropass-back`, configurados a mano (FR-018).

**Independent Test**: el dashboard filtrado por `simulated` muestra las cifras que imprimió `seed_demo`, y un error forzado envía el correo de B1.

**Depende de**: Phase 9, porque hacen falta datos en Sentry para elegir métricas en la UI, y de un deploy con `/health` para el monitor de Uptime.

- [ ] T045 [US7] (manual) En Sentry → `aeropass-back` → Settings → Security & Privacy: activar "Prevent Storing of IP Addresses" y confirmar que el Data Scrubbing por defecto está activo. Registrar en `specs/002-observabilidad-sentry/contracts/dashboard-and-alerts.md` §Estado.
- [ ] T046 [US7] (manual) Configurar en Vercel `SENTRY_DSN`, `SENTRY_ENVIRONMENT` (`prod` en Production, `dev` en Preview) y `SENTRY_TRACES_SAMPLE_RATE` (`0.2` en Production, `1.0` en Preview), redesplegar y verificar quickstart §3 y §4 (evento en menos de 1 minuto; `/health` 200). Si el evento solo llega con el siguiente request, revisar T015 (`wait_until`).
- [ ] T047 [US7] Correr `seed_demo` con `SENTRY_ENVIRONMENT=simulated` (y `--contingencia`) para tener datos con los que crear widgets y monitores.
- [ ] T048 [US7] (manual) Crear el monitor de Uptime sobre `https://<prod>/health` (GET, cada 1 min, timeout 10 s, 3 fallos para abrir, 1 éxito para cerrar, entorno `prod`, correo al equipo `#aeropass-team`) según contracts/dashboard-and-alerts.md. Registrar el ID.
- [ ] T049 [US7] (manual) Crear el dashboard "AeroPass Backend — Observabilidad" con los widgets W1–W11 y el filtro de entorno según el contrato. Si W3 no admite ecuación o W5 no tiene dataset de Uptime, aplicar la alternativa escrita en el contrato y anotarla. Registrar la URL.
- [ ] T050 [US7] (manual) Crear las alertas B1 (issues con `error.unhandled:true`, todos los entornos, 1 correo cada 5 min), B2 (autoservicio < 0,85 en 1 h, solo `prod`; si no admite la ecuación, aplicar la alternativa del contrato), B3 (≥1 apertura de circuit breaker en 10 min, todos los entornos) y B4 (la del monitor de Uptime). Todas con correo al equipo `#aeropass-team`. Registrar los IDs.
- [ ] T051 [US7] Verificar con `seed_demo --solo-error` en `simulated` que llega el correo de B1 en menos de 2 minutos, y que W3, W4, W7 y W8 coinciden con las cifras impresas por la herramienta. Actualizar §Estado del contrato con los resultados.

**Checkpoint**: operación vigilable sin consultar la base de datos.

---

## Phase 11: Polish & Cross-Cutting

- [ ] T052 [P] Extender `tests/unit/test_no_pii_in_logs.py` (o crear `tests/integration/test_no_pii_in_sentry.py`) para que el flujo con `spoof`, `timeout`, outbox caído y `ok` se ejecute con el transporte en memoria. Ningún envelope (error, transacción, log, métrica, breadcrumb) puede contener el número o nombre del documento, `MOCK:`, bytes de imagen, el token del pase, la clave de firma ni `authorization` (SC-003).
- [ ] T053 [P] Actualizar `README.md`: variables de Sentry, dónde vive la configuración (`adapters/observability/`), la regla "solo hooks en el negocio" (constitución 1.1.0, Principio VI), el enlace a los contratos y quitar "Full observability" de la lista de pendientes.
- [ ] T054 Correr `uv run --python 3.11 ruff check .`, `uv run --python 3.11 mypy` y la suite completa (`TEST_DATABASE_URL` para integración). Corregir hasta dejarlo en verde y restaurar `.pgdata/` y los `.pyc` versionados.
- [ ] T055 Medir el impacto del arranque en frío (SC-004): tiempo de importación de `aeropass.main` con y sin `SENTRY_DSN` (`python -X importtime`) y latencia de `GET /health` en el deploy. Anotar los resultados en `specs/002-observabilidad-sentry/research.md` §3.
- [ ] T056 Recorrer `specs/002-observabilidad-sentry/quickstart.md` §1–§7 y marcar lo verificado. §3, §4 y §7 requieren un deploy (T046).
- [ ] T057 Ensayo de la presentación de 15 minutos con los datos de `seed_demo` en `demo` (Historia 9): cronometrar y ajustar el guion del README.

---

## Dependencies & Execution Order

### Fases

- **Setup (1)** → **Foundational (2)**: bloquea todo.
- **US1 (3)**: primera tras la Foundational (MVP).
- **US6 (4)**, **US2 (5)**, **US3 (6)**, **US4 (7)** y **US5 (8)**: dependen solo de la Foundational y pueden ir en paralelo. US5 comparte archivo de prueba con US4 (T036/T040), así que en la práctica va después de US4.
- **US9 (9)**: necesita US4, US5 y US6 (la herramienta demuestra sus métricas).
- **US7 (10)**: necesita US9 (datos) y un deploy con `/health` (T028 + T046).
- **Polish (11)**: al final. T052 puede empezar tras US4.

### Dentro de cada historia

Pruebas → modelo/puerto → servicio/adaptador → endpoint → regla del catálogo.

### Tareas manuales

T045–T050 requieren acceso a Sentry y Vercel. T046 y T048 requieren un deploy con esta rama.

---

## Parallel Example: Foundational

```bash
# Pruebas que se escriben en paralelo (archivos distintos), todas deben fallar antes de T012–T015:
Task: "T007 tests/unit/test_sentry_privacy.py"
Task: "T008 tests/contract/test_telemetry_allowlist.py"
Task: "T009 tests/unit/test_observability_setup.py"
Task: "T010 tests/unit/test_hooks.py"
Task: "T011 tests/unit/test_sentry_sinks.py"
```

## Parallel Example: User Story 6

```bash
Task: "T020 tests/contract/test_health_contract.py"
Task: "T021 tests/unit/test_health_service.py"
Task: "T022 tests/unit/test_circuit_breaker.py"
Task: "T023 tests/integration/test_pass_latency_telemetry.py"
# Y luego:
Task: "T024 src/aeropass/ports/health.py"
Task: "T025 src/aeropass/adapters/health/checks.py"
```

---

## Implementation Strategy

### MVP (US1)

1. Phase 1 + Phase 2: privacidad e inicialización, lo más crítico.
2. Phase 3: errores no controlados en Sentry.
3. **Validar**: con un deploy de preview (T046 parcial), un error aparece en menos de 1 minuto sin datos sensibles.

### Entrega incremental

1. MVP → US6 (salud, latencia y contingencia, P1) → US2/US3 (trazas y auditoría) → US4/US5 (métricas de negocio).
2. US9 (herramienta de demo) → US7 (dashboard y alertas con datos reales o simulados).
3. Polish y ensayo.

### Para la presentación

Si el tiempo aprieta, el camino mínimo es: Foundational → US1 → US6 (T024–T030) → US4 → US5 → US9 → US7 (T047–T051). US2 y US3 funcionan casi solas con la Foundational; sus fases solo agregan pruebas.

---

## Notes

- Ninguna tarea toca la lógica de negocio. Los únicos cambios en servicios y dominio son `estado_final` (T037), la anotación del facade (T038) y la instrumentación del breaker (T029).
- Si un umbral cambia en la UI de Sentry, la UI manda; actualizar el contrato en el siguiente PR.
- Fuera de esta feature: sacar `.pgdata/` y `__pycache__/` de git; la inyección de fallos del anexo C; el KR A2.7; la Historia 8.

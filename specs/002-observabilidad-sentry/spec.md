# Feature Specification: Observabilidad con Sentry (Backend)

**Feature Branch**: `observabilidad`

**Created**: 2026-09-23

**Status**: Draft

**Input**: User description: "Aplicar observabilidad al backend AeroPass con Sentry, cumpliendo el Principio VI de la constitución ('Observabilidad habilitada, no implementada aquí'): conectar los hooks de tracing (`@traced`) y auditoría (`@audited`) ya expuestos en `observability/hooks.py` a Sentry/OpenTelemetry, sin bloquear la implementación funcional existente. El catálogo específico de métricas de negocio a medir será entregado por el usuario en una iteración posterior."

## User Scenarios & Testing *(mandatory)*

### User Story 1 - Captura automática de excepciones no controladas (Priority: P1)

Como responsable de operaciones/soporte del backend, cuando ocurre una excepción no controlada en cualquier endpoint (`identity`, `biometrics`, `passes`, `wellknown`, `internal`), quiero que quede registrada automáticamente en la plataforma de observabilidad con suficiente contexto (ruta, método, identificador de solicitud, pila de llamadas) para diagnosticar el incidente sin tener que reproducirlo manualmente ni pedir logs adicionales al equipo de desarrollo.

**Why this priority**: Sin esto no hay observabilidad real: cualquier falla en producción sería invisible hasta que un pasajero o la aerolínea la reporte. Es el mínimo viable de la funcionalidad.

**Independent Test**: Forzar una excepción no controlada en un endpoint de prueba (o un endpoint real con una entrada inválida no cubierta por las validaciones actuales) y confirmar que el evento aparece en la plataforma de observabilidad con la pila de llamadas, la ruta y el método HTTP, en menos de un minuto.

**Acceptance Scenarios**:

1. **Given** el backend está desplegado con la plataforma de observabilidad configurada, **When** un endpoint lanza una excepción no controlada, **Then** el evento queda visible en la plataforma de observabilidad con la pila de llamadas completa y el contexto de la solicitud (ruta, método), y la respuesta HTTP al cliente no se ve alterada respecto al comportamiento actual.
2. **Given** la plataforma de observabilidad NO está configurada (entorno local sin credencial), **When** ocurre la misma excepción, **Then** el backend responde exactamente igual que hoy (sin envío de telemetría, sin errores adicionales, sin cambios de comportamiento funcional).

---

### User Story 2 - Trazas del pipeline crítico (Priority: P2)

Como ingeniero de plataforma, quiero ver una traza (span) por cada paso ya instrumentado con `@traced` del pipeline (documento → verificación biométrica → creación de identidad → emisión de credencial/QR, y cada llamada a un servicio externo inestable), para identificar cuellos de botella, pasos lentos y fallos parciales sin añadir instrumentación nueva a la lógica de negocio.

**Why this priority**: El código ya expone los puntos de extensión (`@traced`, `@audited`) precisamente para esto; conectar Sentry aquí es lo que convierte el "hook vacío" documentado en la constitución en observabilidad real, sin tocar los servicios.

**Independent Test**: Ejecutar un flujo de registro/verificación/emisión de QR de extremo a extremo (puede ser con adaptadores fake) y confirmar que la traza resultante en la plataforma de observabilidad muestra un span por cada paso decorado con `@traced`, con su duración.

**Acceptance Scenarios**:

1. **Given** un flujo de verificación de identidad completo, **When** este se ejecuta, **Then** la plataforma de observabilidad muestra una traza con un span por cada paso instrumentado (`@traced`), incluyendo las llamadas a proveedores externos protegidas por circuit breaker (Principio V).
2. **Given** un paso del pipeline fallando (p. ej. el proveedor de biometría con timeout), **When** el circuit breaker actúa, **Then** el span correspondiente refleja el fallo/timeout de forma distinguible de un fallo exitoso, sin necesidad de logs adicionales para saberlo.

---

### User Story 3 - Auditoría correlacionable sin datos sensibles (Priority: P3)

Como encargado de cumplimiento/seguridad, quiero que cada evento de auditoría (`@audited`) emitido por el dominio (p. ej. credencial emitida, validación fallida) quede registrado en la plataforma de observabilidad como un evento correlacionable con su resultado (éxito/error), sin exponer nunca datos sensibles (documento, biometría, tokens de credencial, claves de firma).

**Why this priority**: Es el requisito de cumplimiento que hace segura la observabilidad misma; sin esta garantía, activar Sentry sería un riesgo nuevo en vez de una mejora.

**Independent Test**: Disparar un evento auditado (éxito y error) y confirmar en la plataforma de observabilidad que el evento aparece con su nombre y resultado, y que ningún campo del payload contiene datos prohibidos.

**Acceptance Scenarios**:

1. **Given** un servicio decorado con `@audited` completa su operación exitosamente, **When** el evento se emite, **Then** aparece en la plataforma de observabilidad como un evento correlacionable de resultado "ok", sin datos personales ni biométricos.
2. **Given** la misma operación falla, **When** el evento se emite, **Then** aparece como un evento de resultado "error" con el tipo de excepción, sin exponer el mensaje crudo si este pudiera contener datos sensibles.

---

### User Story 4 - Tasa de autoservicio (KR A1.2, Priority: P2)

Como responsable de negocio, quiero medir qué porcentaje de las validaciones se resuelve completamente sin intervención de un agente humano (registro, verificación biométrica y emisión de credencial/pase completados por el propio sistema), para verificar el cumplimiento del Key Result A1.2 ("Tasa de autoservicio", meta ≥85%) que sustenta la reducción de fricción prometida a aeropuertos y aerolíneas.

**Why this priority**: Es el KR que más directamente conecta la observabilidad técnica con el caso de negocio: el mismo evento de auditoría (`@audited`) que ya se conecta a Sentry en la Historia 3 es la fuente de esta métrica, sin instrumentación adicional.

**Independent Test**: Ejecutar un lote de flujos de verificación —algunos exitosos de principio a fin y otros que terminan en un estado que hoy requeriría intervención (p. ej. rechazo repetido)— y confirmar que la métrica resultante refleja la proporción correcta de autoservicio.

**Acceptance Scenarios**:

1. **Given** un conjunto de solicitudes de verificación en un periodo, **When** se calcula la tasa de autoservicio, **Then** el resultado es (verificaciones completadas sin intervención ÷ verificaciones totales del periodo) × 100, derivado de los eventos de auditoría ya emitidos por `identity_verification_facade`/`biometric_verification_service`.
2. **Given** la tasa cae por debajo del umbral de negocio (85%), **When** esto ocurre, **Then** el dato está disponible en la plataforma de observabilidad sin necesidad de una consulta manual a la base de datos.

---

### User Story 5 - Tasa de auto rechazo (Priority: P2)

Como responsable de negocio y de calidad de verificación, quiero medir qué porcentaje de los intentos de verificación biométrica son rechazados automáticamente por el sistema (documento inválido, comparación facial insuficiente, liveness fallida), para distinguir cuánta fricción proviene del propio sistema de verificación frente a la que proviene de otras causas, e informar al Key Result A2.3 (falsos rechazos).

**Why this priority**: Junto con su contraparte en la app móvil (donde ocurre el primer rechazo, del lado del dispositivo), esta métrica identifica en qué capa se concentra el rechazo automático — dato que hoy no es visible sin instrumentación.

**Independent Test**: Ejecutar intentos de verificación que resulten en aceptación y en rechazo (por distintas causas ya modeladas en el dominio) y confirmar que la tasa resultante distingue el rechazo automático de un error técnico no controlado.

**Acceptance Scenarios**:

1. **Given** un conjunto de intentos de verificación biométrica en el backend, **When** se calcula la métrica, **Then** el resultado es (intentos rechazados automáticamente por regla de negocio ÷ intentos totales) × 100, y se distingue explícitamente de los rechazos causados por un error técnico no controlado (Historia 1).

---

### User Story 6 - Disponibilidad, latencia y contingencia (Priority: P1)

Como responsable de operaciones, quiero ver la disponibilidad del servicio en horario operativo (KR A2.1, ≥99,9%), la latencia de emisión y consulta de pase (referencia del KR A2.4 dentro del alcance actual de este repo) y la tasa de aperturas de circuit breaker por dependencia externa (KR A2.5, Principio V), para verificar que el backend cumple la confiabilidad que un entorno aeroportuario exige.

**Why this priority**: Es la base técnica de la que depende el objetivo de negocio A2 completo; sin esto no se puede demostrar la confiabilidad exigida para operar en puntos de control.

**Independent Test**: Consultar la plataforma de observabilidad durante una ventana de operación normal y durante una simulación de fallo de un proveedor externo, y confirmar que las tres métricas (disponibilidad, latencia, tasa de contingencia) reflejan cada situación correctamente.

**Acceptance Scenarios**:

1. **Given** el backend operando con normalidad, **When** se consulta la plataforma de observabilidad, **Then** se puede leer la disponibilidad del periodo y la latencia p95/p99 de emisión y consulta de pase.
2. **Given** un proveedor externo (p. ej. biometría) deja de responder y el circuit breaker se abre, **When** esto ocurre, **Then** la tasa de contingencia del periodo aumenta de forma visible y queda asociada a la dependencia específica que falló.

---

### User Story 7 - Panel y alertas operativas en Sentry (Priority: P1)

Como responsable de operaciones, quiero un panel (dashboard) en el proyecto de Sentry `aeropass-back` (organización Aeropass, ya existente) que muestre de un vistazo las métricas de las Historias 1–6, y reglas de alerta por correo electrónico que avisen cuando alguna cruce su umbral de negocio, para enterarme de una degradación sin tener que estar consultando la plataforma activamente.

**Why this priority**: Instrumentar sin poder verlo ni ser alertado equivale a no tener observabilidad; el panel y las alertas son lo que convierte los datos capturados en las Historias 1–6 en una operación vigilable.

**Independent Test**: Abrir el proyecto `aeropass-back` en Sentry y verificar que el dashboard muestra las métricas priorizadas; forzar una condición de alerta (p. ej. simular varias aperturas de circuit breaker seguidas) y confirmar que llega el correo correspondiente.

**Acceptance Scenarios**:

1. **Given** el proyecto `aeropass-back` en la organización Aeropass, **When** se abre su dashboard, **Then** se pueden leer sin configuración adicional: la tasa de excepciones no controladas, la tasa de autoservicio, la tasa de auto rechazo, la disponibilidad, la latencia p95/p99 de emisión/consulta de pase y la tasa de aperturas de circuit breaker.
2. **Given** una regla de alerta configurada para uno de los umbrales de negocio (p. ej. disponibilidad <99,9% en la ventana operativa, o tasa de autoservicio <85%), **When** el umbral se cruza, **Then** llega un correo electrónico con la información suficiente para identificar qué métrica y qué componente lo causó, sin exponer datos sensibles del payload que la originó.
3. **Given** un pico de excepciones no controladas o de aperturas de circuit breaker en un periodo corto, **When** esto ocurre, **Then** una regla de alerta lo notifica por correo antes de que el operador necesite revisar el dashboard proactivamente.

---

### User Story 8 - Métricas fuera de alcance en este repo (Priority: N/A, diferida)

Dos de las métricas de negocio propuestas por el usuario —el tiempo medio de escalamiento a un agente humano y el costo de fallover/contingencia— dependen de un componente (cola de escalamiento + consola de agente humano, equivalente al "punto de control"/`validate` que el Principio II de la constitución declara fuera de alcance de este repo, a cargo de otro equipo) que todavía no existe en ninguno de los dos repositorios de AeroPass.

**Why this priority**: Se documenta explícitamente en vez de omitirse en silencio, siguiendo la misma disciplina que el Principio II exige para el resto del alcance del backend.

**Independent Test**: Diferido hasta que el componente de escalamiento/consola exista; en ese momento, el tiempo de escalamiento se mide desde el encolado del caso hasta su resolución, y el costo de contingencia se deriva de la métrica técnica "tasa de contingencia" (Historia 6) más una tarifa de costo que aporta negocio.

**Acceptance Scenarios**:

1. **Given** el componente de escalamiento/consola sea implementado por el equipo responsable, **When** se mida el tiempo de escalamiento, **Then** se reutiliza el mismo mecanismo de trazas (`@traced`) ya conectado en este spec, sin requerir un segundo sistema de observabilidad.

---

### Edge Cases

- ¿Qué pasa si `SENTRY_DSN` no está configurado (desarrollo local o CI)? El sistema debe operar exactamente igual que hoy, sin intentar enviar datos y sin fallar por su ausencia.
- ¿Qué pasa si la plataforma de observabilidad no está disponible o responde con timeout? El envío de telemetría nunca debe bloquear ni degradar una solicitud de negocio, ni consumir tiempo de ejecución relevante en un entorno serverless con cold starts (Principio I).
- ¿Qué pasa cuando el circuit breaker de un proveedor externo (Principio V) se abre? Debe quedar registrado como una categoría de evento distinguible de un error de código genérico.
- ¿Qué pasa si un payload de error contiene por accidente un dato sensible (p. ej. un token en un mensaje de excepción)? Debe existir un mecanismo de saneamiento antes del envío, no depender de la disciplina de cada punto de instrumentación.
- ¿Qué pasa en un cold start (primera invocación del proceso serverless)? La inicialización de la observabilidad no debe repetirse ni fallar en invocaciones "warm" subsecuentes dentro del mismo proceso.
- ¿Qué pasa si alguien cambia un umbral de alerta directamente en la UI de Sentry sin actualizar este spec? La UI de Sentry es la fuente de verdad operativa de los umbrales configurados; este spec documenta qué debe alertarse y con qué meta de referencia (los KR), no el valor exacto vigente en cada momento.

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: El sistema DEBE capturar automáticamente toda excepción no controlada en cualquier endpoint HTTP y enviarla a la plataforma de observabilidad con el contexto de la solicitud (ruta, método, identificador de solicitud) y la pila de llamadas.
- **FR-002**: El sistema DEBE generar una traza (span) por cada paso ya instrumentado con `@traced` en los servicios y adaptadores existentes, sin requerir cambios en la lógica de negocio de esos puntos.
- **FR-003**: El sistema DEBE registrar cada evento emitido por `@audited` como un evento correlacionable con su nombre y resultado (éxito/error).
- **FR-004**: El sistema DEBE permitir desactivar por completo el envío a la plataforma de observabilidad mediante configuración (ausencia de credencial), operando de forma funcionalmente idéntica al modo activado, salvo por la ausencia de telemetría.
- **FR-005**: El sistema DEBE etiquetar cada evento enviado con el entorno de despliegue (development/staging/production) para permitir filtrar por entorno.
- **FR-006**: El sistema NO DEBE incluir en ningún evento, traza o breadcrumb enviado a la plataforma de observabilidad: números o imágenes de documento de identidad, plantillas o muestras biométricas, tokens de credencial o payloads de QR, claves de firma, ni cabeceras de autorización/credenciales de proveedores.
- **FR-007**: El envío de telemetría a la plataforma de observabilidad NUNCA DEBE bloquear, retrasar de forma perceptible, ni hacer fallar una solicitud de negocio, incluyendo el caso en que la plataforma de observabilidad no esté disponible.
- **FR-008**: El sistema DEBE registrar la apertura de un circuit breaker (Principio V) como una categoría de evento distinguible de un error de código no controlado.
- **FR-009**: La inicialización de la observabilidad DEBE ser segura de ejecutar en cada invocación de un proceso serverless (incluyendo cold starts) sin duplicar registros ni fallar en invocaciones repetidas dentro del mismo proceso.
- **FR-010**: La instrumentación DEBE permitir incorporar métricas de negocio adicionales reutilizando los mismos puntos de extensión (`@traced`/`@audited`), sin requerir un mecanismo distinto ni refactorizar los servicios existentes.
- **FR-011**: El sistema DEBE calcular y exponer en la plataforma de observabilidad la tasa de autoservicio (KR A1.2): la proporción de verificaciones completadas sin intervención humana sobre el total de verificaciones de un periodo.
- **FR-012**: El sistema DEBE calcular y exponer la tasa de auto rechazo del backend: la proporción de intentos de verificación biométrica rechazados automáticamente por una regla de negocio (documento inválido, comparación facial insuficiente, liveness fallida) sobre el total de intentos, distinguida de los rechazos por error técnico no controlado (FR-001).
- **FR-013**: El sistema DEBE exponer la disponibilidad del servicio durante el horario operativo (KR A2.1) y la latencia p95/p99 de emisión y consulta de pase (referencia del KR A2.4 dentro del alcance actual del backend).
- **FR-014**: El sistema DEBE exponer la tasa de aperturas de circuit breaker (KR A2.5/Principio V) por dependencia externa, en una ventana de tiempo, distinguiendo qué dependencia falló.
- **FR-015**: El sistema NO DEBE intentar medir el tiempo de escalamiento a un agente humano ni el costo de contingencia mientras el componente de cola de escalamiento y consola de agente (fuera de alcance, Principio II) no exista; estas métricas quedan documentadas como diferidas (User Story 8) en vez de omitidas.
- **FR-016**: DEBE existir un dashboard en el proyecto de Sentry `aeropass-back` (organización Aeropass) que muestre, sin configuración adicional por parte de quien lo consulta, las métricas de las Historias 1–6: tasa de excepciones no controladas, tasa de autoservicio, tasa de auto rechazo, disponibilidad, latencia p95/p99 de emisión/consulta de pase, y tasa de aperturas de circuit breaker.
- **FR-017**: DEBEN existir reglas de alerta por correo electrónico en Sentry para, como mínimo: disponibilidad por debajo de 99,9% en la ventana operativa, tasa de autoservicio por debajo de 85%, y un volumen anómalo de excepciones no controladas o de aperturas de circuit breaker en una ventana corta.
- **FR-018**: Las alertas y el dashboard DEBEN configurarse de forma manual en la interfaz de Sentry para este ciclo (no como código versionado en el repositorio); un cambio de umbral se aplica directamente ahí.

### Key Entities

- **Evento de error**: una excepción no controlada capturada por el sistema; incluye pila de llamadas, ruta/método de la solicitud y entorno, y nunca datos sensibles.
- **Traza (span)**: la representación de un paso instrumentado del pipeline (o de una llamada externa); incluye nombre del paso, duración y resultado (éxito/fallo/circuito-abierto).
- **Evento de auditoría**: el registro correlacionable de una operación de negocio auditada (`@audited`); incluye nombre del evento y resultado, sin payload de negocio sensible.
- **Métrica de negocio/técnica derivada**: un valor agregado (tasa, latencia p95/p99, disponibilidad) calculado a partir de eventos de auditoría y trazas ya existentes, sin persistencia adicional de datos sensibles.
- **Regla de alerta**: un umbral configurado en Sentry sobre una métrica derivada, con un canal de notificación (correo electrónico) y un destinatario.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: El 100% de las excepciones no controladas ocurridas en cualquier endpoint quedan visibles en la plataforma de observabilidad en menos de 1 minuto.
- **SC-002**: El tiempo para identificar el paso del pipeline y el componente responsable de un incidente de producción se reduce a minutos, sin necesitar reproducir el error manualmente ni desplegar logs adicionales.
- **SC-003**: Cero incidentes de fuga de datos sensibles (documento, biometría, tokens) a través de la plataforma de observabilidad, verificable mediante una auditoría de muestreo de los payloads enviados.
- **SC-004**: La latencia adicional introducida por la instrumentación es imperceptible para el llamante (no introduce un nuevo timeout ni una degradación medible respecto a los tiempos de respuesta actuales).
- **SC-005**: Cuando la plataforma de observabilidad no está disponible, el 100% de las solicitudes de negocio se completan igual que si la observabilidad estuviera activa (ninguna solicitud falla por causa de la telemetría).
- **SC-006**: Añadir una métrica de negocio nueva sobre un paso ya instrumentado no requiere modificar el mecanismo de observabilidad (`observability/hooks.py`) ni el código de los servicios que ya usan `@traced`/`@audited`.
- **SC-007**: La tasa de autoservicio (KR A1.2) es consultable en la plataforma de observabilidad sin necesidad de una consulta manual a la base de datos, con una meta de negocio de ≥85%.
- **SC-008**: La tasa de auto rechazo del backend es consultable por separado de la tasa de errores técnicos no controlados, permitiendo distinguir fricción del sistema de verificación de fallas de código.
- **SC-009**: La disponibilidad del servicio en horario operativo, la latencia p95/p99 de emisión/consulta de pase y la tasa de aperturas de circuit breaker son consultables en la plataforma de observabilidad para cualquier ventana de tiempo, con metas de referencia de ≥99,9% de disponibilidad (KR A2.1).
- **SC-010**: Un responsable de operaciones puede conocer el estado de las 6 métricas priorizadas sin pedirle una consulta a un desarrollador, abriendo únicamente el dashboard del proyecto `aeropass-back`.
- **SC-011**: Cuando una métrica cruza su umbral de negocio, el responsable de operaciones se entera por correo electrónico sin haber estado consultando el dashboard en ese momento.

## Assumptions

- Sentry (junto con OpenTelemetry) es la plataforma de observabilidad mandatada por el Principio I de la constitución; este spec cubre la conexión de los hooks ya expuestos a esa plataforma, no la elección del proveedor.
- Las 3 métricas de negocio priorizadas (tasa de autoservicio, tasa de conversión/finalización de onboarding, tasa de auto rechazo) y las 3 métricas técnicas (disponibilidad, latencia, tasa de contingencia) fueron seleccionadas por el usuario de un catálogo más amplio; la tasa de conversión/finalización de onboarding es responsabilidad principal de la app móvil (ver su spec de observabilidad) y no se repite aquí.
- El tiempo de escalamiento a agente humano y el costo de fallover/contingencia quedan explícitamente diferidos (User Story 8): dependen de un componente de escalamiento/consola que el Principio II declara fuera de alcance de este repo y que hoy no existe en ninguno de los dos repositorios de AeroPass.
- El entorno de desarrollo local y CI no requieren una cuenta activa de la plataforma de observabilidad; su ausencia deshabilita el envío sin generar errores.
- La organización de Sentry ("Aeropass") y el proyecto `aeropass-back` ya existen (creados por el usuario); este spec no cubre su creación, solo lo que debe verse en su dashboard y qué debe alertar.
- El destinatario exacto del correo de alerta lo define el usuario al crear cada regla en la UI de Sentry; este spec fija qué debe alertarse y con qué umbral de referencia (los KR citados), no la dirección de correo.
- La retención y el plan de la plataforma de observabilidad se rigen por su configuración contratada por separado; está fuera de alcance de este spec.
- El dashboard y las reglas de alerta se configuran manualmente en la UI de Sentry en este ciclo (decisión explícita del usuario); no se versionan como código (Terraform/API) por ahora, quedando esa opción disponible para un ciclo posterior si la operación lo justifica.
- Este spec no modifica el comportamiento funcional de ningún endpoint ni servicio existente; únicamente conecta los puntos de extensión de observabilidad ya definidos por el Principio VI.

### Recomendaciones adicionales para un paquete de observabilidad completo (fuera de las historias de usuario, a valorar por el usuario)

- **Sampling de trazas con costo en mente**: dado que el modelo de negocio opera con márgenes ajustados por validación (Sección 4 del caso de negocio), fijar una tasa de muestreo de trazas (no 100%) desde el inicio evita una factura de Sentry que crezca linealmente con el volumen de validaciones, sin perder visibilidad de errores (que sí deben capturarse al 100%).
- **Cron Monitors** de Sentry para cualquier tarea programada (p. ej. el script de despacho de eventos QStash), de forma que una ejecución que no llega a dispararse se detecte igual que un error activo.
- **Scrubbing de datos a nivel de proyecto en Sentry** (Data Scrubbing / PII settings del proyecto `aeropass-back`) como segunda capa de defensa, además del saneamiento hecho en el código (FR-006) — para que un dato sensible que se escape del código quede igualmente bloqueado antes de guardarse.

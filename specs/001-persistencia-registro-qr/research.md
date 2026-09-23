# Research: Persistencia del flujo de registro, verificación biométrica y QR

**Feature**: `001-persistencia-registro-qr` | **Date**: 2026-09-23

Cada decisión sigue el formato Decision / Rationale / Alternatives considered. No quedan
`NEEDS CLARIFICATION` en el Technical Context del plan.

## R1. Acceso a Neon Postgres en serverless

- **Decision**: SQLAlchemy 2.x en modo async con el driver `asyncpg`, contra el endpoint
  **pooled** de Neon (PgBouncer en modo transacción). Un único `AsyncEngine` por proceso
  (Singleton) con `pool_size=1`, `max_overflow=4`, `pool_pre_ping=True`, `pool_recycle=300` y
  `connect_args={"statement_cache_size": 0}` + `prepared_statement_cache_size=0` en la URL
  (obligatorio con PgBouncer en modo transacción). Migraciones con Alembic usando la URL
  **directa** (no pooled) de Neon.
- **Rationale**: el ORM da unidades de trabajo y bloqueos de fila (`SELECT … FOR UPDATE`)
  necesarios para contar intentos y revocar credenciales sin carreras; asyncpg es el driver async
  más rápido. El pooler de Neon absorbe las muchas instancias efímeras de Vercel; el engine
  Singleton reutiliza la conexión mientras la instancia está caliente y `pool_pre_ping` recupera
  conexiones rotas tras un congelamiento.
- **Alternatives considered**: asyncpg puro (menos código, pero repetiría mapeo y unidades de
  trabajo — viola DRY); driver HTTP serverless de Neon (no tiene cliente Python oficial maduro y
  pierde transacciones interactivas); `NullPool` (una conexión nueva por petición: +30–80 ms).

## R2. Almacenamiento de selfies (Vercel Blob)

- **Decision**: store de Vercel Blob con acceso **privado** (`access: private`) para la selfie
  (`selfies/{pasajero_id}/{intento_id}-{sufijo}.{ext}`) y para la foto del rostro del documento
  (`documentos/{pasajero_id}/rostro-{sufijo}.{ext}`), con timeout de **3 s** por operación. Lectura solo desde el servidor
  con `BLOB_READ_WRITE_TOKEN` (o `VERCEL_OIDC_TOKEN`). El acceso va detrás del puerto
  `MediaStorage`; la implementación `VercelBlobStorage` usa el SDK Python de Vercel si expone
  `put/get/delete` de Blob, y si no, una capa delgada `httpx` sobre la API HTTP de Blob. El
  cliente HTTP es Singleton.
- **Rationale**: la documentación de Vercel confirma el almacenamiento privado con autenticación
  por Bearer token, lo que cumple FR-010. El puerto aísla la duda sobre la madurez del SDK Python.
- **Alternatives considered**: store público con URL no adivinable (seguridad por oscuridad,
  no cumple FR-010); subida directa desde el cliente con token de cliente (el servidor tendría que
  confiar en una URL enviada por el cliente, y la foto no pasaría por la validación del servidor).

## R3. Tamaño máximo de las imágenes

- **Decision**: máximo **4 MB** por imagen (selfie o foto del documento; JPEG, PNG o WebP),
  validado por cabecera `Content-Length`, `Content-Type` y firma de bytes antes de subirla. Se
  ajustó el edge case del spec (antes 5 MB).
- **Rationale**: el cuerpo de una petición a una Vercel Function tiene un límite de 4,5 MB; con el
  encabezado multipart, 5 MB no cabe. Una selfie comprimida en el cliente pesa típicamente
  menos de 1 MB.
- **Alternatives considered**: subida directa del cliente a Blob (ver R2, descartada).

## R4. Proveedor biométrico (prueba de vida + comparación)

- **Decision**: puerto `BiometricProvider.evaluate(selfie: ImageInput, referencia: ImageInput) ->
  BiometricResult` (score de liveness, score de comparación, proveedor), donde `referencia` es la
  foto del rostro del documento guardada en el registro (FR-001a). Implementaciones:
  `VisionProviderAdapter` (HTTP genérico vía `httpx`, traduce la respuesta del proveedor al
  modelo de dominio) y `MockBiometricAdapter` (determinista: decide por un marcador ASCII
  `MOCK:ok|spoof|other|timeout` incrustado en los bytes de la selfie; sin marcador → `ok`). Se
  eligen con `BiometricProviderFactory` según `BIOMETRIC_PROVIDER=mock|vision`. Timeout de **4 s**
  por llamada.
- **Presupuesto de latencia (SC-007, < 10 s)**: la subida de la selfie y la descarga de la foto de
  referencia van en paralelo (`asyncio.gather`, 3 s máx.) y luego el proveedor (4 s máx.): peor
  caso ~7 s + base de datos, con margen para el arranque en frío.
- **Rationale**: Factory + Adapter exigidos por la constitución; el proveedor real aún no está
  contratado, así que el mock permite avanzar y probar.
- **Alternatives considered**: acoplar un proveedor concreto (Rekognition, Azure Face) —
  prematuro; verificación WASM en el cliente — descartada en la clarificación (se evalúa en servidor).

## R5. Circuit Breaker

- **Decision**: implementación propia `CircuitBreaker` (estados CLOSED / OPEN / HALF_OPEN) con
  almacenamiento de estado pluggable: `RedisBreakerStateStore` (Upstash Redis, compartido entre
  instancias) en producción e `InMemoryBreakerStateStore` en pruebas. Parámetros por defecto: se
  abre con 5 fallos en una ventana de 60 s, permanece abierto 30 s, y en HALF_OPEN deja pasar una
  sola llamada de prueba. Se aplica al proveedor biométrico y al publicador QStash. Si el breaker
  está abierto, el proveedor biométrico devuelve `NO_CONCLUYENTE` al instante y el publicador deja
  el evento pendiente en el outbox.
- **Rationale**: las bibliotecas (`pybreaker`, `aiobreaker`, `purgatory`) guardan el estado en
  memoria o en redis-py; en serverless el estado en memoria se pierde con cada arranque en frío
  (constitución, Principio V). Un breaker propio con puerto de estado cabe en ~100 líneas y es
  totalmente testeable.
- **Alternatives considered**: `purgatory` con backend Redis (usa el protocolo Redis y no el
  cliente REST de Upstash; añade una segunda forma de conectarse a Redis).

## R6. Upstash Redis: token de uso único y límite de emisión

- **Decision**: cliente `upstash-redis` (async, sobre HTTP REST) como Singleton.
  - Token: clave `qr:{jti}` = `ACTIVA`, con `SET … EX <ttl> NX`, donde el TTL es igual a la
    expiración de la credencial.
  - Revocación: `DEL qr:{jti}`.
  - Consumo (punto de extensión para el checkpoint): script Lua atómico: si el valor es `ACTIVA`,
    lo cambia a `CONSUMIDA` con `KEEPTTL` y devuelve 1; en otro caso devuelve 0 (RN-06).
  - Límite de emisión: `upstash-ratelimit` con ventana deslizante de 30 emisiones / 60 s por
    `pasajero_id`.
- **Rationale**: el cliente REST no mantiene conexiones abiertas, ideal para serverless; el script
  Lua garantiza atomicidad del consumo aunque dos lecturas lleguen a la vez.
- **Alternatives considered**: `GETDEL` (no deja rastro de que la credencial fue consumida —
  se pierde la distinción entre consumida y expirada); redis-py por TLS (conexiones persistentes
  que no sobreviven al congelamiento de la instancia).

## R7. Publicación de `credencial.emitida` sin perder eventos (QStash)

- **Decision**: **Transactional outbox**.
  1. La IdentidadDigital y la fila `outbox_eventos` (estado `PENDIENTE`) se insertan en la
     **misma transacción**.
  2. Tras el commit, se intenta publicar de inmediato con `qstash` (`AsyncQStash.message.publish_json`)
     con timeout de 2 s, a través del circuit breaker, usando `deduplication_id = evento.id`. Si
     tiene éxito, la fila pasa a `ENTREGADO`; si falla, queda `PENDIENTE` y la respuesta al
     pasajero no se afecta.
  3. Un **schedule de QStash** (cada minuto) llama a `POST /internal/outbox/dispatch`, firmado por
     QStash, que reintenta los eventos pendientes con backoff exponencial `min(2^intentos, 180)` s
     (peor caso tras la recuperación: 180 s + 60 s del schedule = 4 min, dentro de SC-006) y marca como
     `EXPIRADA` las credenciales vencidas.
- **Rationale**: garantiza FR-013 (el evento no se pierde) y SC-006 (entrega en menos de 5 min
  tras recuperarse QStash) sin depender de tareas en segundo plano después de la respuesta, que el
  runtime Python de Vercel no garantiza. La deduplicación de QStash y el `id` del evento permiten a
  los consumidores procesarlo una sola vez.
- **Alternatives considered**: `BackgroundTasks` de FastAPI (la función puede congelarse al
  responder y perder el envío); Vercel Cron (en el plan Hobby solo es diario; el schedule de
  QStash mantiene todo dentro del stack de la constitución).
- **Nota sobre el nombre del evento**: el spec (FR-012) publica `credencial.emitida` al crear la
  IdentidadDigital. Se conserva ese nombre; el contenido incluye
  `tipo_credencial: "IDENTIDAD_DIGITAL"` para no confundirlo con la emisión de cada QR, que **no**
  genera evento (a 30–60 s por QR con renovación automática produciría ruido).

## R8. Firma de la CredencialAcceso

- **Decision**: token compacto **JWS con EdDSA (Ed25519)** vía `PyJWT[crypto]`. Claims: `jti`
  (id de credencial), `sub` (pasajero_id), `flt` (código de vuelo), `perms`, `iat`, `exp`, `kid`.
  Clave privada en la variable de entorno `QR_SIGNING_PRIVATE_KEY` (PEM); la clave pública se
  expone en `GET /.well-known/jwks.json`. El QR codifica el JWS.
- **Rationale**: la firma asimétrica permite que el checkpoint (otro equipo) verifique la firma
  sin compartir secretos y también en modo contingencia/offline (Strategy, constitución). Ed25519
  produce firmas cortas (64 bytes), adecuadas para un QR legible.
- **Alternatives considered**: HMAC-SHA256 (obliga a compartir el secreto con cada checkpoint);
  RSA (firmas de 256+ bytes, QR más denso).

## R9. Autenticación con Clerk

- **Decision**: dependencia FastAPI `get_current_user` que valida el session token de Clerk con
  `clerk-backend-api` (`authenticate_request`) y devuelve `AuthenticatedUser(clerk_user_id)`. El
  JWKS se cachea por proceso (es una caché, no estado de negocio). `/internal/*` no usa Clerk: se
  valida la firma de QStash (`Upstash-Signature`) con `qstash.Receiver`.
- **Rationale**: SDK oficial; sin API Gateway, cada router declara su dependencia de seguridad.
- **Alternatives considered**: PyJWT + JWKS manual (válido como fallback; más código propio).

## R10. Concurrencia e integridad

- **Decision**:
  - Unicidad de documento: `UNIQUE (tipo_documento, numero_documento)`.
  - Una IdentidadDigital activa por pasajero: índice único parcial `WHERE estado = 'ACTIVA'`.
  - Una credencial no final por pasajero y vuelo: índice único parcial sobre
    `(pasajero_id, codigo_vuelo) WHERE estado IN ('EMITIDA','ACTIVA')`. La renovación revoca la
    anterior y crea la nueva en la **misma transacción**, con `SELECT … FOR UPDATE` sobre el
    pasajero.
  - Intentos fallidos: el contador se incrementa con el pasajero bloqueado (`FOR UPDATE`). El
    bloqueo se toma **solo** en la transacción final de registro del intento, nunca mientras se
    sube la imagen o se llama al proveedor (evita retener conexiones del pooler de Neon hasta
    ~7 s); dentro de esa transacción se revalida el estado del pasajero.
  - Consumo (RN-06): `UPDATE … SET estado='CONSUMIDA' WHERE id=:id AND estado='ACTIVA'` más el
    script Lua en Redis; si ninguno aplica, la transición se rechaza y se registra.
  - Transiciones rechazadas: los métodos del agregado devuelven un `TransitionResult` en lugar de
    lanzar, para que el rollback del UnitOfWork no borre la fila del rechazo (FR-019).
  - Redis tras commit: `DEL qr:{jti}` de la credencial revocada se ejecuta después del commit;
    si falla, el token caduca solo en ≤ 60 s y Postgres ya la marca `REVOCADA`.
  - Historial inmutable: `transiciones_credencial` solo admite `INSERT` (un trigger rechaza
    `UPDATE` y `DELETE`).
- **Rationale**: las restricciones en la base de datos son la última línea de defensa frente a
  carreras entre instancias serverless.

## R11. Pruebas

- **Decision**: `pytest` + `pytest-asyncio` + `httpx.AsyncClient` (ASGI).
  - **Unit**: dominio (State, Builder, reglas de intentos) y servicios con dobles en memoria de
    todos los puertos (`src/aeropass/adapters/fakes/`, también usados con `AEROPASS_ADAPTERS=fake`).
  - **Integration**: contra un Postgres real (contenedor `postgres:16` local o una rama de Neon
    vía `TEST_DATABASE_URL`), con Redis/Blob/QStash falsos.
  - **Contract**: validación de respuestas contra `contracts/openapi.yaml` y de eventos contra
    `contracts/events/*.json` (`jsonschema`).
- **Rationale**: los fakes de puertos hacen las pruebas rápidas y deterministas; las
  restricciones SQL (índices parciales, trigger) solo se prueban con Postgres real.

## R12. Despliegue y empaquetado

- **Decision**: `pyproject.toml` gestionado con `uv`; entrypoint FastAPI `api/index.py`
  (reexporta `app` desde `aeropass.main`); `vercel.json` con reescritura de todas las rutas a
  `api/index.py` y `excludeFiles` para `tests/**`. La región de la función se fija junto a la
  región de Neon (p. ej. `gru1` ↔ `aws-sa-east-1` para usuarios en Colombia), igual que la
  región del store de Blob y de la base de Upstash.
- **Rationale**: cada salto entre regiones añade 100+ ms, lo que comprometería SC-002.

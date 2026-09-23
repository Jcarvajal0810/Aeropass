<!--
Sync Impact Report
==================
Version change: (template, unversioned) → 1.0.0
Bump rationale: first ratification of the constitution; all placeholders replaced.

Modified principles (template placeholder → new title):
  - [PRINCIPLE_1_NAME] → I. Stack Técnico Canónico (Serverless en Vercel)
  - [PRINCIPLE_2_NAME] → II. Alcance Delimitado con Puntos de Extensión
  - [PRINCIPLE_3_NAME] → III. SOLID en Servicios (NO NEGOCIABLE)
  - [PRINCIPLE_4_NAME] → IV. DRY: Reglas de Negocio con Fuente Única
  - [PRINCIPLE_5_NAME] → V. Circuit Breaker en Toda Dependencia Externa Inestable
Added principles:
  - VI. Observabilidad Habilitada, No Implementada Aquí

Added sections:
  - Patrones de Diseño Obligatorios (replaces [SECTION_2_NAME])
  - Flujo de Desarrollo y Puertas de Calidad (replaces [SECTION_3_NAME])
  - Governance (filled)

Removed sections: none

Dependent templates (read the constitution at runtime; not modified by this command):
  - .specify/templates/plan-template.md      ⚠ pending review ("Constitution Check" gate)
  - .specify/templates/spec-template.md      ⚠ pending review
  - .specify/templates/tasks-template.md     ⚠ pending review

Deferred TODOs: none. RATIFICATION_DATE set to first adoption date (2026-09-23).
-->

# AeroPass Backend Constitution

## Core Principles

### I. Stack Técnico Canónico (Serverless en Vercel)

El backend de AeroPass MUST construirse exclusivamente sobre el siguiente stack. Cualquier
documento de referencia escrito para AWS/Next.js se traduce a este stack antes de usarse:

| Responsabilidad | Tecnología obligatoria | Reemplaza a |
|---|---|---|
| Lenguaje / framework | Python 3.11+ con FastAPI | Next.js ("Route Handler" → *path operation* de FastAPI) |
| Base de datos relacional | Neon Postgres | Aurora / RDS Postgres (modelo y SQL sin cambios) |
| Almacenamiento de medios (selfies, documentos) | Vercel Blob | S3 |
| Mensajería asíncrona | Upstash QStash (entrega HTTP con reintentos) | EventBridge / SQS |
| Caché rápida / uso único del QR | Upstash Redis | ElastiCache |
| Autenticación | Clerk, como dependencia/middleware de FastAPI | Cognito |
| Despliegue | Vercel, runtime Python serverless | Lambda / API Gateway |
| Observabilidad | OpenTelemetry + Sentry (ver Principio VI) | CloudWatch / X-Ray |

Reglas derivadas:

- No existe un API Gateway separado: cada endpoint de FastAPI MUST aplicar sus propias
  validaciones (autenticación Clerk, autorización, validación de entrada).
- QStash es entrega HTTP punto a punto con reintentos, NO pub-sub por patrones; los
  consumidores MUST ser endpoints HTTP idempotentes.
- El código MUST NOT asumir que el proceso permanece vivo entre peticiones. Se espera
  arranque en frío (al menos en la primera llamada del día); ningún estado de negocio
  puede residir solo en memoria del proceso.

**Rationale:** el equipo despliega en Vercel con servicios gestionados; fijar el stack
evita traducciones ambiguas desde la arquitectura de referencia AWS.

### II. Alcance Delimitado con Puntos de Extensión

Este repo implementa ÚNICAMENTE:

1. **Registro** — escaneo de documento de identidad → endpoint `identity` → Neon Postgres.
2. **Verificación biométrica** — captura de selfie → endpoint `biometrics` → Vercel Blob +
   proveedor de visión externo por HTTP, o verificación WASM en cliente con doble-check
   obligatorio en servidor.
3. **Creación de identidad digital** — evento publicado vía QStash tras verificación exitosa.
4. **Generación del QR dinámico** — endpoint `passes` → Upstash Redis (token de uso único)
   + QStash.

Fuera de alcance en este ciclo (a cargo de otros compañeros): validación en checkpoint
(endpoint `validate`), consola del agente humano y cola de escalamiento, observabilidad
completa, e integración con aerolínea/GDS (endpoint `flights`).

- El código fuera de alcance MUST NOT implementarse aquí.
- El diseño MUST dejar interfaces abstractas y contratos de eventos definidos para que
  esas partes se conecten después sin refactorizar el código existente.

**Rationale:** el trabajo se reparte entre equipos; los contratos estables permiten
integrar sin bloqueos mutuos.

### III. SOLID en Servicios (NO NEGOCIABLE)

- Cada clase de servicio MUST tener una única responsabilidad.
- Las dependencias MUST inyectarse por interfaz/abstracción (vía `Depends` de FastAPI o
  constructor); la lógica de negocio MUST NOT instanciar a mano clientes ni adaptadores
  concretos.
- Añadir un nuevo proveedor, estado o paso de verificación MUST lograrse agregando código
  nuevo, no modificando las clases existentes (abierto/cerrado).

**Rationale:** permite sustituir proveedores (reales ↔ mock) y conectar las partes fuera
de alcance sin tocar el núcleo.

### IV. DRY: Reglas de Negocio con Fuente Única

- Ninguna regla de negocio (estados y transiciones de la credencial, validación de
  firma, expiración del QR, reglas RN-xx) MUST repetirse en más de un lugar.
- Cada regla vive en un único servicio u objeto de dominio y todo lo demás lo consume.

**Rationale:** una regla duplicada diverge; en un sistema de acceso a aeropuerto eso es
un fallo de seguridad.

### V. Circuit Breaker en Toda Dependencia Externa Inestable

- Toda llamada a un servicio externo de latencia variable o inestable — proveedor de
  visión biométrica, API de aerolínea/GDS, QStash — MUST pasar por un circuit breaker con
  timeout explícito.
- Ante fallos repetidos el circuito MUST abrirse y el sistema MUST responder con un
  fallback o degradación controlada (p. ej. estado "verificación pendiente", error
  explícito reintentable) en lugar de bloquearse esperando.
- El estado del circuito MUST NOT depender solo de memoria del proceso si eso lo anula
  bajo arranques en frío; cuando sea relevante se persiste en Upstash Redis.

**Rationale:** en serverless una espera colgada consume el tiempo de ejecución y degrada
toda la experiencia del pasajero.

### VI. Observabilidad Habilitada, No Implementada Aquí

- La observabilidad completa (OpenTelemetry + Sentry, métricas técnicas/de negocio,
  simulación de fallos) es responsabilidad de otro equipo.
- Este backend MUST exponer los hooks necesarios (spans alrededor de cada paso del
  pipeline y de cada llamada externa, puntos de emisión de métricas) sin que su ausencia
  bloquee la implementación funcional.

**Rationale:** desacopla el avance de este equipo del de observabilidad sin perder
trazabilidad futura.

## Patrones de Diseño Obligatorios

Los siguientes patrones MUST aplicarse donde se indica. "Documentado" significa que la
interfaz o el contrato se define en este repo aunque la implementación completa dependa
de una parte fuera de alcance.

**Creacionales**

- **Factory / Abstract Factory:** instanciación de adaptadores de proveedor
  (`VisionProviderAdapter`, `MockBiometricAdapter`, `AirlineApiAdapter`) sin acoplar el
  servicio principal a una implementación concreta.
- **Builder:** construcción paso a paso de la `CredencialAcceso` (QR) — pasajero, vuelo,
  permisos, firma digital, expiración de 30–60 s. El builder MUST validar cada regla antes
  de emitir; una credencial incompleta o inválida MUST NOT poder construirse.
- **Singleton:** clientes de Upstash Redis, pool de Neon Postgres y cliente de Vercel
  Blob — una instancia reutilizada por proceso, nunca una por petición.

**Estructurales**

- **Adapter:** capa de integración con proveedores externos heterogéneos (visión,
  aerolíneas) que traduce su formato al modelo de dominio unificado (`Tiquete`, `Vuelo`);
  ningún detalle externo MUST filtrarse al núcleo.
- **Facade:** `IdentityVerificationFacade` con la interfaz
  `verify_and_create_identity(pasajero, doc, selfie)`, que oculta el pipeline documento →
  liveness → comparación → creación de identidad.
- **Proxy:** `RedisVerificationProxy` intercepta la verificación rápida antes de tocar
  Neon Postgres para mantener baja la latencia.
- **Decorator:** envuelve la lógica base de autorización con capacidades adicionales
  (métrica, traza de auditoría, notificación) sin modificar la función base.

**Comportamentales**

- **Strategy:** alternancia entre validación en línea y modo contingencia/offline sin
  tocar el código cliente (documentado; aplica al checkpoint).
- **Observer / Event-driven vía QStash:** los eventos `credencial.emitida` y
  `validacion.fallida` se publican y los interesados (auditoría, escalamiento) reaccionan
  sin acoplamiento directo. Los esquemas de evento MUST estar versionados y definidos en
  este repo.
- **State:** la `CredencialAcceso` vive en los estados `EMITIDA`, `ACTIVA`, `CONSUMIDA`,
  `EXPIRADA`, `REVOCADA`. Las transiciones inválidas (p. ej. RN-06: una credencial ya
  `CONSUMIDA` no puede volver a consumirse) MUST encapsularse en el propio objeto de
  estado, no en condicionales dispersos.
- **Chain of Responsibility:** cadena de verificación previa a autorizar — token válido
  → rate limit → vuelo no cancelado (RN-07) → firma del QR válida → uso único respetado
  (RN-06). Documentada para el checkpoint; su implementación completa depende de la parte
  fuera de alcance.

## Flujo de Desarrollo y Puertas de Calidad

- Toda especificación y plan (`/speckit-specify`, `/speckit-plan`) MUST pasar el
  "Constitution Check": stack del Principio I, alcance del Principio II, y patrones
  aplicables de la sección anterior.
- Toda revisión de código MUST verificar: inyección de dependencias por abstracción
  (III), ausencia de reglas duplicadas (IV), circuit breaker y timeout en cada llamada
  externa (V), y hooks de instrumentación presentes (VI).
- Los adaptadores externos MUST tener una implementación mock intercambiable vía Factory
  para pruebas locales sin credenciales reales.
- Cualquier desviación de un principio o patrón MUST justificarse por escrito en la
  sección "Complexity Tracking" del plan correspondiente.

## Governance

- Esta constitución prevalece sobre cualquier otra práctica o documento de referencia
  del proyecto (incluida la arquitectura de referencia AWS/Next.js).
- **Enmiendas:** se proponen mediante `/speckit-constitution`, se documentan en el Sync
  Impact Report al inicio de este archivo y requieren aprobación del equipo antes de
  hacer merge.
- **Versionado semántico:** MAJOR para eliminación o redefinición incompatible de
  principios; MINOR para principios o secciones nuevas o guía materialmente ampliada;
  PATCH para aclaraciones y redacción.
- **Cumplimiento:** cada PR y cada plan MUST verificar el cumplimiento; los cambios de
  alcance (p. ej. incorporar `validate` o `flights` a este repo) requieren enmienda del
  Principio II.

**Version**: 1.0.0 | **Ratified**: 2026-09-23 | **Last Amended**: 2026-09-23

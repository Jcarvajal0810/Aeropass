<!--
Sync Impact Report
==================
Version change: 1.0.0 → 1.1.0
Bump rationale: MINOR. Observability moves into this repo's scope (Principle II) and Principle VI is
materially redefined from "hooks only" to "implemented behind the hooks"; Principle I gains a
clarification. No principle is removed and no existing rule becomes incompatible.
Approved by: the user, for feature 002-observabilidad-sentry (2026-09-24).

Modified principles:
  - I. Canonical Tech Stack (Serverless on Vercel): observability row clarified (Sentry SDK
    satisfies "OpenTelemetry + Sentry" while instrumentation goes through the hooks)
  - II. Bounded Scope with Extension Points: observability added to the in-scope list and removed
    from the out-of-scope list
  - VI. Observability Enabled, Not Implemented Here → VI. Observability Behind Hooks
Added sections: none
Removed sections: none

Dependent templates (read the constitution at runtime; not modified by this command):
  - .specify/templates/plan-template.md      ✅ no change needed (generic "Constitution Check")
  - .specify/templates/spec-template.md      ✅ no change needed
  - .specify/templates/tasks-template.md     ✅ no change needed
  - specs/002-observabilidad-sentry/plan.md  ⚠ Constitution Check rows I, II and VI to be marked
    resolved (feature artifact, updated outside this command)

Deferred TODOs: none.

Previous report (1.0.0)
-----------------------
Version change: (template, unversioned) → 1.0.0
Bump rationale: first ratification of the constitution; all placeholders replaced.

Modified principles (template placeholder → new title):
  - [PRINCIPLE_1_NAME] → I. Canonical Tech Stack (Serverless on Vercel)
  - [PRINCIPLE_2_NAME] → II. Bounded Scope with Extension Points
  - [PRINCIPLE_3_NAME] → III. SOLID in Services (NON-NEGOTIABLE)
  - [PRINCIPLE_4_NAME] → IV. DRY: Business Rules with a Single Source
  - [PRINCIPLE_5_NAME] → V. Circuit Breaker on Every Unstable External Dependency
Added principles:
  - VI. Observability Enabled, Not Implemented Here

Added sections:
  - Mandatory Design Patterns (replaces [SECTION_2_NAME])
  - Development Workflow and Quality Gates (replaces [SECTION_3_NAME])
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

### I. Canonical Tech Stack (Serverless on Vercel)

The AeroPass backend MUST be built exclusively on the following stack. Any reference document
written for AWS/Next.js is translated to this stack before being used:

| Responsibility | Mandatory technology | Replaces |
|---|---|---|
| Language / framework | Python 3.11+ with FastAPI | Next.js ("Route Handler" → FastAPI *path operation*) |
| Relational database | Neon Postgres | Aurora / RDS Postgres (model and SQL unchanged) |
| Media storage (selfies, documents) | Vercel Blob | S3 |
| Asynchronous messaging | Upstash QStash (HTTP delivery with retries) | EventBridge / SQS |
| Fast cache / QR single use | Upstash Redis | ElastiCache |
| Authentication | Clerk, as a FastAPI dependency/middleware | Cognito |
| Deployment | Vercel, serverless Python runtime | Lambda / API Gateway |
| Observability | OpenTelemetry + Sentry (see Principle VI) | CloudWatch / X-Ray |

Derived rules:

- The observability row is satisfied by the Sentry SDK used directly, as long as every span, audit
  record and metric goes through the hooks of Principle VI. Moving to OpenTelemetry MUST then only
  require replacing the registered sink, never touching services or domain code.
- There is no separate API Gateway: each FastAPI endpoint MUST apply its own validations (Clerk
  authentication, authorization, input validation).
- QStash is point-to-point HTTP delivery with retries, NOT pattern-based pub-sub; consumers MUST
  be idempotent HTTP endpoints.
- Code MUST NOT assume the process stays alive between requests. Cold starts are expected (at
  least on the first call of the day); no business state may live only in process memory.

**Rationale:** the team deploys on Vercel with managed services; fixing the stack avoids
ambiguous translations from the AWS reference architecture.

### II. Bounded Scope with Extension Points

This repo implements ONLY:

1. **Registration** — identity document scan → `identity` endpoint → Neon Postgres.
2. **Biometric verification** — selfie capture → `biometrics` endpoint → Vercel Blob + external
   vision provider over HTTP, or WASM verification on the client with a mandatory double-check on
   the server.
3. **Digital identity creation** — event published via QStash after a successful verification.
4. **Dynamic QR generation** — `passes` endpoint → Upstash Redis (single-use token) + QStash.
5. **Observability** — the sinks that connect the Principle VI hooks to Sentry (isolated in
   `adapters/observability/`), the unauthenticated health endpoint (`/health`) and the tooling that
   prepares demo telemetry data.

Out of scope in this cycle (handled by other teammates): checkpoint validation (`validate`
endpoint), human agent console and escalation queue, and airline/GDS integration (`flights`
endpoint).

- Out-of-scope code MUST NOT be implemented here.
- The design MUST leave abstract interfaces and defined event contracts so those parts can be
  plugged in later without refactoring the existing code.

**Rationale:** the work is split between teams; stable contracts allow integration without
mutual blocking.

### III. SOLID in Services (NON-NEGOTIABLE)

- Each service class MUST have a single responsibility.
- Dependencies MUST be injected by interface/abstraction (via FastAPI `Depends` or the
  constructor); business logic MUST NOT instantiate concrete clients or adapters by hand.
- Adding a new provider, state or verification step MUST be achieved by adding new code, not by
  modifying existing classes (open/closed).

**Rationale:** allows swapping providers (real ↔ mock) and plugging in the out-of-scope parts
without touching the core.

### IV. DRY: Business Rules with a Single Source

- No business rule (credential states and transitions, signature validation, QR expiration,
  RN-xx rules) MUST be repeated in more than one place.
- Each rule lives in a single service or domain object and everything else consumes it.

**Rationale:** a duplicated rule diverges; in an airport access system that is a security flaw.

### V. Circuit Breaker on Every Unstable External Dependency

- Every call to an external service with variable or unstable latency — biometric vision
  provider, airline/GDS API, QStash — MUST go through a circuit breaker with an explicit timeout.
- On repeated failures the circuit MUST open and the system MUST respond with a fallback or
  controlled degradation (e.g. "verification pending" status, explicit retryable error) instead
  of blocking while waiting.
- The circuit state MUST NOT depend only on process memory if that defeats it under cold starts;
  when relevant it is persisted in Upstash Redis.

**Rationale:** in serverless a hung wait consumes execution time and degrades the whole passenger
experience.

### VI. Observability Behind Hooks

- Observability (errors, traces, audit records, technical and business metrics) is implemented in
  this repo **behind the hooks** of `observability/hooks.py`: `@traced`, `@audited`, `span()` and
  `emit_audit()`.
- Domain, services and API code MUST only use those hooks. They MUST NOT import the observability
  provider's SDK; only `adapters/observability/` does.
- Every pipeline step and every external call MUST be instrumented with a hook (spans around each
  step and each external call, audit records at metric emission points).
- Telemetry MUST NEVER block, noticeably delay or fail a business request, including when the
  provider is unavailable. Without provider credentials the system MUST behave exactly as without
  observability.
- No telemetry MAY carry identity document data or images, biometric samples or scores, credential
  tokens or QR payloads, signing keys or authorization headers.
- Failure simulation (fault injection) remains out of scope in this cycle.

**Rationale:** the hooks keep business code independent of the provider, so observability can be
added, changed or removed without touching the core; the privacy and non-blocking rules make the
observability itself safe to run in an airport identity system.

## Mandatory Design Patterns

The following patterns MUST be applied where indicated. "Documented" means that the interface or
contract is defined in this repo even though the full implementation depends on an out-of-scope
part.

**Creational**

- **Factory / Abstract Factory:** instantiation of provider adapters (`VisionProviderAdapter`,
  `MockBiometricAdapter`, `AirlineApiAdapter`) without coupling the main service to a concrete
  implementation.
- **Builder:** step-by-step construction of the `CredencialAcceso` (QR) — passenger, flight,
  permissions, digital signature, 30–60 s expiration. The builder MUST validate every rule before
  issuing; an incomplete or invalid credential MUST NOT be buildable.
- **Singleton:** Upstash Redis clients, Neon Postgres pool and Vercel Blob client — one instance
  reused per process, never one per request.

**Structural**

- **Adapter:** integration layer with heterogeneous external providers (vision, airlines) that
  translates their format into the unified domain model (`Tiquete`, `Vuelo`); no external detail
  MUST leak into the core.
- **Facade:** `IdentityVerificationFacade` with the interface
  `verify_and_create_identity(pasajero, doc, selfie)`, which hides the pipeline document →
  liveness → comparison → identity creation.
- **Proxy:** `RedisVerificationProxy` intercepts the fast verification before touching Neon
  Postgres to keep latency low.
- **Decorator:** wraps the base authorization logic with additional capabilities (metrics, audit
  trail, notification) without modifying the base function.

**Behavioral**

- **Strategy:** switching between online validation and contingency/offline mode without touching
  the client code (documented; applies to the checkpoint).
- **Observer / Event-driven via QStash:** the `credencial.emitida` and `validacion.fallida` events
  are published and interested parties (audit, escalation) react without direct coupling. Event
  schemas MUST be versioned and defined in this repo.
- **State:** the `CredencialAcceso` lives in the states `EMITIDA`, `ACTIVA`, `CONSUMIDA`,
  `EXPIRADA`, `REVOCADA`. Invalid transitions (e.g. RN-06: an already `CONSUMIDA` credential
  cannot be consumed again) MUST be encapsulated in the state object itself, not in scattered
  conditionals.
- **Chain of Responsibility:** verification chain before authorizing — valid token → rate limit →
  flight not cancelled (RN-07) → valid QR signature → single use respected (RN-06). Documented for
  the checkpoint; its full implementation depends on the out-of-scope part.

## Development Workflow and Quality Gates

- Every specification and plan (`/speckit-specify`, `/speckit-plan`) MUST pass the "Constitution
  Check": stack from Principle I, scope from Principle II, and applicable patterns from the
  previous section.
- Every code review MUST verify: dependency injection by abstraction (III), absence of duplicated
  rules (IV), circuit breaker and timeout on every external call (V), and instrumentation hooks
  present (VI).
- External adapters MUST have a mock implementation swappable via Factory for local tests without
  real credentials.
- Any deviation from a principle or pattern MUST be justified in writing in the "Complexity
  Tracking" section of the corresponding plan.

## Governance

- This constitution prevails over any other practice or reference document of the project
  (including the AWS/Next.js reference architecture).
- **Amendments:** proposed via `/speckit-constitution`, documented in the Sync Impact Report at
  the top of this file and require team approval before merging.
- **Semantic versioning:** MAJOR for removal or incompatible redefinition of principles; MINOR for
  new principles or sections or materially expanded guidance; PATCH for clarifications and
  wording.
- **Compliance:** every PR and every plan MUST verify compliance; scope changes (e.g. bringing
  `validate` or `flights` into this repo) require an amendment of Principle II.

**Version**: 1.1.0 | **Ratified**: 2026-09-23 | **Last Amended**: 2026-09-24

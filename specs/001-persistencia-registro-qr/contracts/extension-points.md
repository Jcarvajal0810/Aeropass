# Extension Points: interfaces for other teams' modules

These interfaces are defined and implemented (or left as a contract) in this feature so that the
out-of-scope modules can plug in without refactoring (constitution, Principle II).

## 1. Credential consumption — checkpoint module

`CredentialLifecycleService.consume(jti: UUID, actor: str) -> ConsumeResult`

- Applies the `ACTIVA → CONSUMIDA` transition in Postgres (`UPDATE … WHERE estado = 'ACTIVA'`) and
  in Redis (atomic Lua script on `qr:{jti}`).
- `ConsumeResult`: `CONSUMIDA` | `YA_CONSUMIDA` (RN-06) | `EXPIRADA` | `REVOCADA` | `NO_ENCONTRADA`.
- Every call, accepted or rejected, leaves a row in `transiciones_credencial`; a rejection does not
  raise an exception, so the row is committed before the result is returned.
- It is **not** exposed over HTTP in this feature; the checkpoint team will create the `validate`
  endpoint and call this service. The validation chain (Chain of Responsibility: token →
  rate limit → flight not cancelled RN-07 → signature → single use RN-06) will be built there; the
  last link is this method.

## 2. QR signature verification — online or offline checkpoint

- Format: compact JWS, `alg: EdDSA`, `crv: Ed25519`, `kid` header.
- Claims: `jti` (credential uuid), `sub` (pasajero_id), `flt` (flight code),
  `perms` (list), `iat`, `exp` (epoch s; `exp - iat` between 30 and 60).
- Public keys: `GET /.well-known/jwks.json` (cacheable; enables offline validation — Strategy).
- Shared utility: `aeropass.domain.credential.signing.CredentialVerifier` (single source of the
  signing rule, DRY).

## 3. Events — audit and escalation

| Event | Schema | Producer |
|---|---|---|
| `credencial.emitida` v1 | `contracts/events/credencial.emitida.v1.json` | This feature |
| `validacion.fallida` v1 | `contracts/events/validacion.fallida.v1.json` (defined here; `ConsumeResult` maps to its `motivo`) | Checkpoint (out of scope) |

- Subscription: each consumer registers its URL as a destination in QStash (URL group
  `aeropass-eventos`); the publisher does not know the consumers (Observer).
- Guarantee: at-least-once delivery; deduplicate by `id`.
- New events are added with a new versioned schema and a new `tipo`; the publisher does not
  change (open/closed).

## 4. Escalation — human agent console

- Passengers in `REQUIERE_REVISION_MANUAL` can be queried by status
  (`PassengerRepository.list_by_estado`). The console (out of scope) will define how to leave
  that status; this feature does not implement that transition.

## 5. External providers

- `BiometricProvider` (port) + `BiometricProviderFactory`: adding a provider = new adapter
  class + registration in the factory.
- `AirlineApiAdapter` (integration with `flights`/GDS): out of scope; when it exists, it will
  validate `codigo_vuelo` in `PassIssuanceService` through an injected `FlightCatalog` port, whose
  current implementation (`FormatOnlyFlightCatalog`) only validates the format.

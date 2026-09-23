# Quickstart Run: 001-persistencia-registro-qr

**Date**: 2026-09-23
**Environment**: Windows 11, Python 3.12.14 (uv), embedded PostgreSQL 16.2 (`pgserver`),
`AEROPASS_ADAPTERS=fake`, `BIOMETRIC_PROVIDER=mock`, Ed25519 key generated with `gen_signing_key`.

## Automated tests

- [x] `uv run pytest`: **163 passed**, 0 failed and 0 skipped. Includes unit, contract and
  integration tests against real Postgres.
- [x] `uv run ruff check .`, `uv run ruff format --check .` and `uv run mypy`: no findings.

Key quickstart tests:

| Test | Result |
|---|---|
| `test_credential_states.py` (full table, RN-06) | ✅ |
| `test_credential_builder.py` | ✅ |
| `test_concurrent_consume.py` (50 consumptions → 1 accepted; SC-004) | ✅ |
| `test_concurrent_passes.py` | ✅ |
| `test_outbox_recovery.py` (30 min simulated outage → delivery < 4 min; SC-006) | ✅ |
| `test_rejected_transition_persisted.py` (FR-019) | ✅ |
| `test_circuit_breaker.py` + `test_biometric_circuit_breaker.py` | ✅ |
| `test_transitions_immutable.py` | ✅ |

## Manual walkthrough (uvicorn on `:8765`, steps 1–8)

The walkthrough passed in full: **16/16 checks OK**.

| Step | Result |
|---|---|
| 1. Registration | 201 `PENDIENTE_VERIFICACION`; `pasajeros` stores only `documentos/{id}/rostro-….jpg`; resubmission returns 200 with the same id; expired → 422; no photo → 422 |
| 2. `spoof` selfie | `FALLIDO` / `LIVENESS`, 2 remaining |
| 3. Provider down | 5 × `NO_CONCLUYENTE` without consuming attempts; the 6th responds in **0.118 s** because the circuit is open |
| 4. `ok` selfie (after 31 s) | `EXITOSO` and `VERIFICADO` with `identidad_id`; `credencial.emitida` event `ENTREGADO`; the attempt stores only `selfies/{id}/{intento}-….jpg` |
| 5. Pass | 201; EdDSA JWS verified with `/.well-known/jwks.json` (kid `qr-dev`); TTL 45 s; history `EMITIDA → ACTIVA` |
| 6. Renewal | Previous pass `REVOCADA` with reason `RENOVACION` |
| 7. Expiration (46 s) | `EXPIRADA` |
| 8. Limit | 429 `LIMITE_EMISION_EXCEDIDO` with `Retry-After: 11`. There were 28 × 201, because the 2 earlier issuances were still inside the 60 s window |

## Validation on Vercel — PENDING

- [ ] Preview deployment with real services (Neon, private Blob, Upstash Redis, QStash and Clerk).
- [ ] QStash schedule (`setup_qstash_schedule`).
- [ ] Steps 1–6 with a real Clerk token.
- [ ] Blob URL without token → 401/403 (FR-010).
- [ ] `bench_passes`: p95 < 1 s (SC-002).

**Reason**: requires deploying to Vercel and provisioning external services with the team's
credentials. It was not run without explicit authorization.

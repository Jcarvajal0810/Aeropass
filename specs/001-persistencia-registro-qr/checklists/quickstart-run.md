# Quickstart Run: 001-persistencia-registro-qr

**Fecha**: 2026-09-23
**Entorno**: Windows 11, Python 3.12.14 (uv), PostgreSQL 16.2 embebido (`pgserver`),
`AEROPASS_ADAPTERS=fake`, `BIOMETRIC_PROVIDER=mock`, clave Ed25519 generada con `gen_signing_key`.

## Pruebas automáticas

- [x] `uv run pytest`: **163 passed**, 0 fallidas y 0 omitidas. Incluye unitarias, de contrato y de
  integración contra Postgres real.
- [x] `uv run ruff check .`, `uv run ruff format --check .` y `uv run mypy`: sin hallazgos.

Pruebas clave del quickstart:

| Prueba | Resultado |
|---|---|
| `test_credential_states.py` (tabla completa, RN-06) | ✅ |
| `test_credential_builder.py` | ✅ |
| `test_concurrent_consume.py` (50 consumos → 1 aceptado; SC-004) | ✅ |
| `test_concurrent_passes.py` | ✅ |
| `test_outbox_recovery.py` (30 min de caída simulada → entrega < 4 min; SC-006) | ✅ |
| `test_rejected_transition_persisted.py` (FR-019) | ✅ |
| `test_circuit_breaker.py` + `test_biometric_circuit_breaker.py` | ✅ |
| `test_transitions_immutable.py` | ✅ |

## Recorrido manual (uvicorn en `:8765`, pasos 1–8)

El recorrido pasó completo: **16/16 verificaciones OK**.

| Paso | Resultado |
|---|---|
| 1. Registro | 201 `PENDIENTE_VERIFICACION`; `pasajeros` guarda solo `documentos/{id}/rostro-….jpg`; el reenvío devuelve 200 con el mismo id; vencido → 422; sin foto → 422 |
| 2. Selfie `spoof` | `FALLIDO` / `LIVENESS`, restan 2 |
| 3. Proveedor caído | 5 × `NO_CONCLUYENTE` sin consumir intentos; el 6.º responde en **0,118 s** porque el circuito está abierto |
| 4. Selfie `ok` (tras 31 s) | `EXITOSO` y `VERIFICADO` con `identidad_id`; evento `credencial.emitida` `ENTREGADO`; el intento guarda solo `selfies/{id}/{intento}-….jpg` |
| 5. Pase | 201; JWS EdDSA verificado con `/.well-known/jwks.json` (kid `qr-dev`); TTL 45 s; historial `EMITIDA → ACTIVA` |
| 6. Renovación | Pase anterior `REVOCADA` con motivo `RENOVACION` |
| 7. Expiración (46 s) | `EXPIRADA` |
| 8. Límite | 429 `LIMITE_EMISION_EXCEDIDO` con `Retry-After: 11`. Hubo 28 × 201, porque las 2 emisiones anteriores seguían dentro de la ventana de 60 s |

## Validación en Vercel — PENDIENTE

- [ ] Despliegue de preview con servicios reales (Neon, Blob privado, Upstash Redis, QStash y Clerk).
- [ ] Schedule de QStash (`setup_qstash_schedule`).
- [ ] Pasos 1–6 con token real de Clerk.
- [ ] URL del blob sin token → 401/403 (FR-010).
- [ ] `bench_passes`: p95 < 1 s (SC-002).

**Motivo**: requiere desplegar a Vercel y aprovisionar servicios externos con credenciales del
equipo. No se ejecutó sin autorización explícita.

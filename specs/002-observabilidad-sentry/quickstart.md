# Quickstart: validar la observabilidad del backend

**Feature**: 002 · **Plan**: [plan.md](plan.md)

Guía de validación de punta a punta. El detalle de qué se envía está en [contracts/](contracts/); aquí solo se verifica.

## Prerrequisitos

- Python 3.11 (el 3.14 por defecto de esta máquina no tiene wheels de `pgserver`): anteponer `uv run --python 3.11` a cada comando.
- Postgres para integración: `TEST_DATABASE_URL` (local o rama de Neon que no sea prod).
- DSN del proyecto `aeropass-back` (Sentry → Settings → Client Keys). Solo en variables de entorno, nunca en el repo.

## §1 Sin DSN nada cambia (FR-004, SC-005)

```bash
uv run --python 3.11 pytest -q
```

Esperado: la suite completa pasa igual que antes de la feature, sin `SENTRY_DSN`. Las pruebas nuevas usan un transporte de Sentry en memoria.

## §2 Privacidad (FR-006, SC-003)

```bash
uv run --python 3.11 pytest -q tests/unit/test_sentry_privacy.py tests/contract/test_telemetry_allowlist.py tests/unit/test_no_pii_in_logs.py
```

Esperado: todos los casos de [privacy-filter.md](contracts/privacy-filter.md) en verde. Luego, en Sentry, abrir un error y una transacción de §3 y confirmar: sin cuerpo de request, sin cabecera `authorization`, sin variables locales en la pila y sin query strings.

## §3 Errores y trazas en un deploy real (SC-001, research §3)

1. En Vercel (preview o dev), configurar `SENTRY_DSN`, `SENTRY_ENVIRONMENT=dev` y `SENTRY_TRACES_SAMPLE_RATE=1.0`, y desplegar.
2. Ejecutar el flujo completo:

```bash
uv run --python 3.11 python -m aeropass.tools.e2e_flow --base-url https://<deploy>
```

3. Esperado en Sentry, en menos de 1 minuto:
   - transacciones `/v1/identity`, `/v1/biometrics/verifications`, `/v1/passes` y `/v1/passes/{credencial_id}` (plantilla de la ruta, sin método), con spans `aeropass.step` (p. ej. `facade.verify_and_create_identity`, `passes.issue`);
   - log `aeropass.audit` con `aeropass.event=identity.verification` y `aeropass.resultado=EXITOSO`;
   - métricas `aeropass.verificacion.intento` y `aeropass.pasajero.estado_final`.
   Si algo llega solo cuando hay un segundo request, el flush con `wait_until` no está funcionando.

## §4 Salud y disponibilidad (FR-013a)

```bash
curl -i https://<deploy>/health
```

Esperado: `200 {"estado":"ok"}` con `Cache-Control: no-store`. Con un `DATABASE_URL` inválido en un deploy de prueba: `503 {"estado":"no_disponible"}`, sin detalles. En Sentry, el monitor de Uptime muestra los chequeos cada minuto.

## §5 Métricas de negocio y contingencia con datos preparados (FR-019, Historia 9)

```bash
SENTRY_DSN=<dsn> SENTRY_ENVIRONMENT=demo DATABASE_URL=<neon-no-prod> \
  uv run --python 3.11 python -m aeropass.tools.seed_demo --contingencia
```

Esperado, filtrando el dashboard por `demo`:
- **W3**: autoservicio igual a la proporción que imprime la herramienta.
- **W4**: auto rechazo por motivo.
- **W6**: latencia de pase.
- **W7**: una apertura de `biometric`.
- **W8**: los `NO_CONCLUYENTE`.
- Llega el correo de B3.

Ejecutarlo al menos 1 hora antes de la presentación.

## §6 Alerta inmediata en vivo (Historia 9, escenario 2)

```bash
SENTRY_DSN=<dsn> SENTRY_ENVIRONMENT=demo uv run --python 3.11 python -m aeropass.tools.seed_demo --solo-error
```

Esperado: un issue nuevo en `aeropass-back` (entorno `demo`) y el correo de B1 en menos de 2 minutos.

## §7 Cold start e idempotencia (FR-009)

En el deploy de §3, hacer dos requests seguidos después de un redeploy. En Sentry no debe haber eventos duplicados, y en los logs de Vercel no debe haber errores de inicialización.

## Estado de verificación (T056, 2026-09-24)

| § | Estado | Evidencia |
|---|---|---|
| §1 | ✅ | `pytest -q` sin `SENTRY_DSN`, con `TEST_DATABASE_URL` sobre un Postgres embebido en un directorio limpio: **281 passed**, sin saltos. |
| §2 | ✅ | Pruebas de privacidad: 22 passed (`test_sentry_privacy`, `test_telemetry_allowlist`, `test_no_pii_in_logs`) + 2 passed (`test_no_pii_in_sentry`). En Sentry, el error `AEROPASS-BACK-2`: mensaje `[redactado]`, sin cuerpo, única cabecera `User-Agent`, sin variables locales y sin query string. Las transacciones `/v1/passes/{credencial_id}` no llevan la URL real (ni UUID ni query), y los spans SQL solo tienen placeholders (`$1::VARCHAR`). |
| §3 | ⚠️ Parcial | El SDK desplegado en `prod` envía datos: 5 transacciones `http.server` (404, sin ruta), `sdk.name=sentry.python.fastapi`, release `f49ddaaf9879`. **Falta** correr `e2e_flow` contra un deploy con `BIOMETRIC_PROVIDER=mock` y confirmar transacciones `/v1/*`, el log de auditoría, las métricas y que un error llegue en menos de 1 minuto. En `prod` todavía no hay tráfico `/v1/*` ni errores del backend. |
| §4 | ✅ | `curl -i https://aeropass-lac.vercel.app/health` → `200`, `Cache-Control: no-store`, `{"estado":"ok"}`. El monitor de Uptime `10437854` registra chequeos cada minuto. El caso 503 no se probó en un deploy; lo cubre `test_health_contract.py`. |
| §5 | ✅ en `simulated` | T047/T051: `seed_demo --contingencia` en `simulated`; W3/W4/W7/W8 coinciden con la herramienta (87,5 % y 36,4 %) y B3 se disparó. En `demo` se corre como preparación de la presentación (T057, al menos 1 h antes). |
| §6 | ✅ en `simulated` | T051: `seed_demo --solo-error` → issue `AEROPASS-BACK-2`; B1 se disparó 26 s después. |
| §7 | ⏳ Pendiente | Necesita un redeploy y acceso a los logs de Vercel. La idempotencia del registro de hooks la cubre `test_observability_setup.py`. |

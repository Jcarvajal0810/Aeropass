# Extension Points: interfaces para los módulos de otros equipos

Estas interfaces se definen e implementan (o se dejan como contrato) en esta feature para que los
módulos fuera de alcance se conecten sin refactorizar (constitución, Principio II).

## 1. Consumo de credencial — módulo de checkpoint

`CredentialLifecycleService.consume(jti: UUID, actor: str) -> ConsumeResult`

- Aplica la transición `ACTIVA → CONSUMIDA` en Postgres (`UPDATE … WHERE estado = 'ACTIVA'`) y
  en Redis (script Lua atómico sobre `qr:{jti}`).
- `ConsumeResult`: `CONSUMIDA` | `YA_CONSUMIDA` (RN-06) | `EXPIRADA` | `REVOCADA` | `NO_ENCONTRADA`.
- Toda llamada, aceptada o rechazada, deja una fila en `transiciones_credencial`; el rechazo no
  lanza excepción, así que la fila se confirma (commit) antes de devolver el resultado.
- **No** se expone por HTTP en esta feature; el endpoint `validate` lo creará el equipo del
  checkpoint y llamará a este servicio. La cadena de validación (Chain of Responsibility: token →
  rate limit → vuelo no cancelado RN-07 → firma → uso único RN-06) se construirá allí; el último
  eslabón es este método.

## 2. Verificación de la firma del QR — checkpoint en línea u offline

- Formato: JWS compacto, `alg: EdDSA`, `crv: Ed25519`, header `kid`.
- Claims: `jti` (uuid de la credencial), `sub` (pasajero_id), `flt` (código de vuelo),
  `perms` (lista), `iat`, `exp` (epoch s; `exp - iat` entre 30 y 60).
- Claves públicas: `GET /.well-known/jwks.json` (cacheable; permite validación offline — Strategy).
- Utilidad compartida: `aeropass.domain.credential.signing.CredentialVerifier` (fuente única de
  la regla de firma, DRY).

## 3. Eventos — auditoría y escalamiento

| Evento | Esquema | Productor |
|---|---|---|
| `credencial.emitida` v1 | `contracts/events/credencial.emitida.v1.json` | Esta feature |
| `validacion.fallida` v1 | `contracts/events/validacion.fallida.v1.json` (definido aquí; `ConsumeResult` mapea a su `motivo`) | Checkpoint (fuera de alcance) |

- Suscripción: cada consumidor registra su URL como destino en QStash (URL group
  `aeropass-eventos`); el publicador no conoce a los consumidores (Observer).
- Garantía: entrega al menos una vez; deduplicar por `id`.
- Nuevos eventos se agregan con un esquema versionado nuevo y un nuevo `tipo`; el publicador no
  cambia (abierto/cerrado).

## 4. Escalamiento — consola del agente humano

- Los pasajeros en `REQUIERE_REVISION_MANUAL` son consultables por estado
  (`PassengerRepository.list_by_estado`). La consola (fuera de alcance) definirá cómo salir de
  ese estado; esta feature no implementa esa transición.

## 5. Proveedores externos

- `BiometricProvider` (puerto) + `BiometricProviderFactory`: agregar un proveedor = nueva clase
  adapter + registro en la factory.
- `AirlineApiAdapter` (integración con `flights`/GDS): fuera de alcance; cuando exista, validará
  `codigo_vuelo` en `PassIssuanceService` mediante un puerto `FlightCatalog` inyectado, cuya
  implementación actual (`FormatOnlyFlightCatalog`) solo valida el formato.

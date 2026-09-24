# Contrato: `GET /health`

**Feature**: 002 · Referencias: [research.md](../research.md) §9 · FR-013, FR-013a, Historia 6

## Petición

`GET /health`, sin autenticación ni parámetros. Lo consulta cada minuto el monitor de Uptime de Sentry.

## Respuestas

| Situación | Código | Cuerpo | Cabeceras |
|---|---|---|---|
| Base de datos y Redis responden dentro del tiempo | `200` | `{"estado": "ok"}` | `Cache-Control: no-store` |
| Algún check falla o tarda más de 3 s | `503` | `{"estado": "no_disponible"}` | `Cache-Control: no-store` |

- El cuerpo **nunca** incluye qué dependencia falló, versiones, hosts ni mensajes de error. Qué check falló queda solo en un log `WARNING` del logger `aeropass.health`, sin detalles de conexión.
- En modo `AEROPASS_ADAPTERS=fake` solo se verifica la base de datos.
- Los checks corren en paralelo, así que el tiempo total es de aproximadamente 3 s en el peor caso.
- Excluido de las trazas (`traces_sampler` = 0) y del esquema OpenAPI público.
- No pasa por Clerk ni por rate limit.

## Checks

| Nombre | Operación | Timeout |
|---|---|---|
| `database` | `SELECT 1` con el `async_sessionmaker` existente | 3 s |
| `redis` | `PING` con `get_redis()` | 3 s |

## Pruebas

- Contrato (`tests/contract/test_health_contract.py`): 200 con todos los checks bien, 503 con uno que lanza y 503 con uno que se cuelga (usando timeout corto). Ninguna respuesta contiene el nombre del check.
- Unitaria (`test_health_service.py`): checks en paralelo; el tiempo total queda acotado por el timeout.

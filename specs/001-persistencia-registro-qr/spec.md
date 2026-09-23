# Feature Specification: Persistencia del flujo de registro, verificación biométrica y QR

**Feature Branch**: `001-persistencia-registro-qr`

**Created**: 2026-09-23

**Status**: Draft

**Input**: User description: "Necesito la persistencia de datos del flujo de registro, verificación
biométrica y generación de QR de AeroPass: (1) registro con datos del documento y estado inicial;
(2) selfie en almacén de medios y resultado de la comparación (éxito/fallo, score) referenciando la
imagen; (3) creación de IdentidadDigital y evento asíncrono `credencial.emitida`; (4) CredencialAcceso
(QR) con pasajero, vuelo, permisos, firma y expiración de 30–60 s, token de uso único en almacén
rápido e historial en la base relacional; estados EMITIDA, ACTIVA, CONSUMIDA, EXPIRADA, REVOCADA;
RN-06 (no se consume dos veces). Fuera de alcance: validación en checkpoint, consola del agente
humano, observabilidad."

## Clarifications

### Session 2026-09-23

- Q: ¿La verificación biométrica debe incluir prueba de vida (liveness) además de la comparación
  selfie vs. documento? → A: Sí, en servidor: el proveedor evalúa liveness y comparación; el intento
  guarda ambos resultados y scores, y solo es `EXITOSO` si ambos superan su umbral.
- Q: ¿El QR se renueva automáticamente mientras el pase está abierto o solo bajo demanda? → A:
  Renovación automática antes de expirar; la credencial anterior pasa a `REVOCADA`; límite de 30
  emisiones por pasajero por minuto.
- Q: ¿Contra qué imagen se compara la selfie, si el documento solo aporta datos de texto? → A: El
  cliente envía en el registro, junto con los datos, la foto del rostro recortada del documento; se
  guarda en el almacén de medios privado y la comparación biométrica usa esa foto como referencia.
  (Decisión por defecto tomada en el análisis de consistencia; ver `/speckit-analyze`, hallazgo U1.)

## User Scenarios & Testing *(mandatory)*

### User Story 1 - Registro del documento de identidad (Priority: P1)

Un pasajero autenticado escanea su documento de identidad. El sistema guarda los datos leídos
(nombre completo, tipo y número de documento, fecha de vencimiento) asociados a su registro de
pasajero, que queda en estado inicial `PENDIENTE_VERIFICACION`. La foto del rostro del documento
se guarda en el almacén de medios privado y el registro solo conserva su referencia.

**Why this priority**: es la puerta de entrada de todo el flujo; sin pasajero registrado no hay
verificación, identidad ni QR.

**Independent Test**: enviar los datos de un documento válido y comprobar que existe un pasajero
recuperable con esos datos y estado `PENDIENTE_VERIFICACION`.

**Acceptance Scenarios**:

1. **Given** un pasajero autenticado sin registro previo, **When** envía los datos y la foto de un
   documento vigente, **Then** se crea un pasajero con esos datos, la referencia a la foto y estado
   `PENDIENTE_VERIFICACION`, y la respuesta devuelve su identificador.
2. **Given** un documento con fecha de vencimiento anterior a hoy, **When** se envía, **Then** el
   registro se rechaza con un motivo explícito ("documento vencido") y no se persiste nada.
3. **Given** un pasajero ya registrado con el mismo tipo y número de documento en la misma cuenta,
   **When** vuelve a enviar el documento, **Then** no se crea un duplicado y se devuelve el
   pasajero existente con su estado actual.
4. **Given** un tipo y número de documento ya asociado a otra cuenta, **When** se intenta
   registrar, **Then** se rechaza con motivo "documento ya registrado".
5. **Given** una cuenta que ya registró un documento, **When** intenta registrar un documento
   distinto, **Then** se rechaza (una cuenta corresponde a un único pasajero).

---

### User Story 2 - Verificación biométrica con selfie (Priority: P1)

El pasajero registrado se toma una selfie. La imagen se almacena en el almacén de medios (nunca en
la base relacional) y el resultado de compararla contra la foto del documento (éxito/fallo y score
de confianza) se guarda como un intento de verificación que referencia la ubicación de la imagen.

**Why this priority**: sin verificación exitosa no puede existir identidad digital; es el control
de seguridad central del producto.

**Independent Test**: con un pasajero en `PENDIENTE_VERIFICACION`, enviar una selfie y comprobar
que la imagen es recuperable desde su referencia, que existe un intento con resultado y score, y
que el pasajero cambia de estado según el resultado.

**Acceptance Scenarios**:

1. **Given** un pasajero `PENDIENTE_VERIFICACION`, **When** envía una selfie cuya prueba de vida y
   comparación superan sus umbrales, **Then** se guarda la imagen, se registra un intento `EXITOSO`
   con ambos scores y la referencia a la imagen, y el pasajero pasa a `VERIFICADO`.
2. **Given** un pasajero `PENDIENTE_VERIFICACION`, **When** la prueba de vida o la comparación no
   supera su umbral (p. ej. foto de una pantalla), **Then** se registra un intento `FALLIDO` con
   ambos scores y el motivo (`LIVENESS` o `COMPARACION`), y el pasajero sigue en
   `PENDIENTE_VERIFICACION` mientras no agote los intentos.
3. **Given** un pasajero que acumula 3 intentos `FALLIDO`, **When** se registra el tercero,
   **Then** el pasajero pasa a `REQUIERE_REVISION_MANUAL` y no acepta más selfies.
4. **Given** que el proveedor de comparación no responde o está degradado, **When** el pasajero
   envía una selfie, **Then** se registra un intento `NO_CONCLUYENTE` que no cuenta como fallo y se
   informa al pasajero que reintente más tarde, sin quedar bloqueado esperando.
5. **Given** que la imagen no se pudo almacenar, **When** el pasajero envía la selfie, **Then** no
   se realiza la comparación ni se registra un intento con referencia inválida.

---

### User Story 3 - Creación de la identidad digital (Priority: P2)

Tras una verificación exitosa, el sistema crea automáticamente la IdentidadDigital del pasajero y
publica el evento `credencial.emitida` para que otros servicios (auditoría, escalamiento) reaccionen
sin que el pasajero espere por ellos.

**Why this priority**: habilita la emisión de QR y la integración con los módulos de otros equipos,
pero depende de las historias 1 y 2.

**Independent Test**: provocar una verificación exitosa y comprobar que existe exactamente una
IdentidadDigital activa para el pasajero y que el evento fue entregado (o quedó pendiente de
entrega) con los datos esperados.

**Acceptance Scenarios**:

1. **Given** un intento `EXITOSO`, **When** se completa, **Then** se crea una IdentidadDigital
   `ACTIVA` vinculada al pasajero y al intento que la originó, y la respuesta al pasajero no espera
   la entrega del evento.
2. **Given** que el canal de eventos no está disponible, **When** se crea la identidad, **Then** la
   identidad queda creada igualmente y el evento queda pendiente y se reintenta hasta entregarse.
3. **Given** un pasajero que ya tiene una IdentidadDigital `ACTIVA`, **When** ocurre otra
   verificación exitosa, **Then** no se crea una segunda identidad activa.

---

### User Story 4 - Generación del QR dinámico (CredencialAcceso) (Priority: P2)

Un pasajero con IdentidadDigital activa solicita su pase para un vuelo. El sistema construye una
CredencialAcceso con su identificador, código de vuelo, permisos, firma digital y expiración de
30 a 60 segundos; registra el token de uso único para verificación rápida y guarda la metadata y el
historial de estados de la credencial.

**Why this priority**: es el entregable visible al pasajero, pero requiere identidad digital previa.

**Independent Test**: con una identidad activa, solicitar un pase y comprobar que el token es
recuperable como no usado en el almacén rápido, que caduca en el plazo definido, que su firma es
verificable y que existe su historial con los estados `EMITIDA` → `ACTIVA`.

**Acceptance Scenarios**:

1. **Given** un pasajero con IdentidadDigital `ACTIVA`, **When** solicita un pase para un código de
   vuelo válido, **Then** se emite una credencial firmada con expiración entre 30 y 60 s, su token
   queda registrado como no usado y su historial muestra `EMITIDA` y luego `ACTIVA`.
2. **Given** un pasajero sin IdentidadDigital activa, **When** solicita un pase, **Then** se
   rechaza y no se crea ninguna credencial.
3. **Given** una credencial `ACTIVA` para el mismo pasajero y vuelo que está por vencer, **When** la
   app solicita automáticamente la renovación, **Then** la anterior pasa a `REVOCADA` (motivo
   `RENOVACION`), su token deja de ser aceptable y se emite una nueva.
4. **Given** una credencial `ACTIVA` cuyo plazo venció sin consumirse, **When** se consulta,
   **Then** figura como `EXPIRADA` y su token ya no es aceptable.
5. **Given** una credencial `CONSUMIDA`, **When** cualquier componente intenta consumirla de nuevo,
   **Then** la transición se rechaza (RN-06) y el intento queda registrado en el historial.
6. **Given** un pasajero que ya obtuvo 30 credenciales en el último minuto, **When** solicita otra,
   **Then** la solicitud se rechaza con indicación de reintentar más tarde y no se crea credencial.

---

### Edge Cases

- Datos del documento incompletos o ilegibles (falta nombre, número, fecha o foto): se rechaza el
  registro indicando los campos faltantes.
- Selfie o foto del documento con formato no admitido o tamaño superior a 4 MB (límite de cuerpo
  de la plataforma de despliegue): se rechaza antes de almacenarla.
- La foto del documento no se pudo almacenar: el registro se rechaza con indicación de reintentar
  y no se persiste el pasajero.
- El estado del pasajero cambia mientras se evalúa su selfie (p. ej. dos selfies enviadas a la
  vez): solo el primer resultado registrado cuenta; el segundo se rechaza y su imagen queda
  huérfana e identificable.
- Selfie enviada por un pasajero ya `VERIFICADO` o `REQUIERE_REVISION_MANUAL`: se rechaza.
- Imagen almacenada pero fallo al registrar el intento: la imagen huérfana queda identificable
  para limpieza posterior y no se considera verificación.
- Documento que vence entre el registro y la solicitud del pase: se rechaza la emisión del pase.
- Dos solicitudes de pase simultáneas para el mismo pasajero y vuelo: solo una credencial queda
  `ACTIVA`; la otra queda `REVOCADA`.
- Arranque en frío del servicio: ningún dato del flujo depende de estado en memoria; todo se
  recupera de los almacenes persistentes.
- Transición inválida sobre una credencial (p. ej. `EXPIRADA` → `ACTIVA`, `REVOCADA` →
  `CONSUMIDA`): se rechaza y queda registrada.

## Requirements *(mandatory)*

### Functional Requirements

**Registro**

- **FR-001**: El sistema MUST persistir, por pasajero, nombre completo, tipo de documento, número
  de documento, fecha de vencimiento, cuenta autenticada propietaria, estado y fechas de creación y
  actualización.
- **FR-001a**: El sistema MUST recibir en el registro la foto del rostro del documento, almacenarla
  en el almacén de medios privado y persistir solo su referencia; es la imagen de referencia para
  la comparación biométrica.
- **FR-002**: El sistema MUST asignar el estado inicial `PENDIENTE_VERIFICACION` a todo pasajero
  nuevo.
- **FR-003**: El sistema MUST rechazar documentos vencidos o con campos obligatorios faltantes, sin
  persistir datos parciales.
- **FR-004**: El sistema MUST garantizar unicidad por tipo + número de documento; un reenvío desde
  la misma cuenta devuelve el pasajero existente y desde otra cuenta se rechaza. Cada cuenta
  corresponde a un único pasajero: registrar un documento distinto desde una cuenta ya registrada
  se rechaza.

**Verificación biométrica**

- **FR-005**: El sistema MUST almacenar cada selfie en el almacén de medios y MUST NOT guardar el
  contenido de la imagen en la base relacional.
- **FR-006**: El sistema MUST registrar cada intento de verificación con: pasajero, referencia a la
  imagen, resultado (`EXITOSO`, `FALLIDO`, `NO_CONCLUYENTE`), motivo de fallo (`LIVENESS`,
  `COMPARACION` o ninguno), score de prueba de vida y score de comparación (0–1, cuando existan),
  umbrales aplicados, proveedor que evaluó y fecha.
- **FR-007**: El sistema MUST evaluar en servidor tanto la prueba de vida (la selfie corresponde a
  una persona real y presente) como la comparación de la selfie contra la foto del documento
  (FR-001a), y MUST considerar exitoso
  un intento solo si ambos scores son iguales o superiores a sus umbrales configurables.
- **FR-008**: El sistema MUST pasar el pasajero a `VERIFICADO` tras un intento exitoso y a
  `REQUIERE_REVISION_MANUAL` al tercer intento `FALLIDO`; los intentos `NO_CONCLUYENTE` no cuentan.
- **FR-009**: Cuando el proveedor de comparación falle o supere el tiempo máximo de espera, el
  sistema MUST responder con resultado `NO_CONCLUYENTE` en lugar de bloquearse.
- **FR-010**: Las imágenes almacenadas MUST NOT ser accesibles públicamente; solo el sistema puede
  recuperarlas mediante la referencia persistida.

**Identidad digital**

- **FR-011**: Tras un intento exitoso, el sistema MUST crear una IdentidadDigital `ACTIVA` que
  referencie al pasajero y al intento que la originó; un pasajero MUST tener como máximo una
  identidad `ACTIVA`.
- **FR-012**: El sistema MUST publicar el evento `credencial.emitida` de forma asíncrona tras crear
  la identidad, sin que la respuesta al pasajero espere su entrega.
- **FR-013**: El sistema MUST garantizar que el evento no se pierda si el canal no está disponible:
  queda registrado como pendiente y se reintenta hasta su entrega; cada evento lleva un
  identificador único para que los consumidores lo procesen una sola vez.
- **FR-014**: El contenido del evento MUST incluir identificador del evento, versión del esquema,
  tipo, identificador de pasajero, identificador de identidad y fecha, y MUST NOT incluir datos
  del documento ni la imagen.

**Credencial de acceso (QR)**

- **FR-015**: El sistema MUST emitir credenciales solo a pasajeros con IdentidadDigital `ACTIVA` y
  documento vigente.
- **FR-016**: Cada credencial MUST contener identificador de pasajero, código de vuelo, permisos,
  firma digital verificable y fecha de expiración entre 30 y 60 segundos (configurable dentro de
  ese rango) desde su emisión; una credencial sin alguno de estos elementos MUST NOT emitirse.
- **FR-017**: El sistema MUST registrar el token de uso único en el almacén rápido con una
  caducidad igual a la de la credencial, marcado como no usado.
- **FR-018**: El sistema MUST persistir la metadata de cada credencial y cada transición de estado
  (estado anterior, estado nuevo, fecha, motivo) como historial inmutable.
- **FR-019**: El sistema MUST admitir solo estas transiciones: `EMITIDA`→`ACTIVA`,
  `EMITIDA`→`REVOCADA`, `EMITIDA`→`EXPIRADA`, `ACTIVA`→`CONSUMIDA`, `ACTIVA`→`EXPIRADA`,
  `ACTIVA`→`REVOCADA`. `CONSUMIDA`, `EXPIRADA` y `REVOCADA` son finales; cualquier otra
  transición, incluida `CONSUMIDA`→`CONSUMIDA` (RN-06), MUST rechazarse y registrarse.
- **FR-020**: El sistema MUST soportar la renovación automática del QR mientras el pase está
  abierto: al emitir una nueva credencial para el mismo pasajero y vuelo, MUST revocar la
  credencial previa no final (motivo `RENOVACION`) y eliminar la validez de su token, de modo que
  en todo momento haya como máximo una credencial `ACTIVA` por pasajero y vuelo.
- **FR-020a**: El sistema MUST limitar la emisión a 30 credenciales por pasajero por minuto y
  rechazar las solicitudes que excedan ese límite sin crear credencial.
- **FR-021**: El sistema MUST exponer la operación de consumo de credencial (con aplicación de
  RN-06) como punto de extensión para el módulo de checkpoint, aunque la validación en checkpoint
  queda fuera de este alcance.

**Transversales**

- **FR-022**: Todas las operaciones MUST exigir un pasajero autenticado y MUST restringir el acceso
  a los datos del propio pasajero.
- **FR-023**: Ninguna operación MUST depender de estado mantenido en memoria entre peticiones.

### Key Entities

- **Pasajero**: persona registrada en AeroPass. Atributos: identificador, cuenta autenticada,
  nombre completo, tipo y número de documento, fecha de vencimiento del documento, referencia a la
  foto del documento, estado
  (`PENDIENTE_VERIFICACION`, `VERIFICADO`, `REQUIERE_REVISION_MANUAL`), fechas. Tiene muchos
  intentos de verificación, como máximo una identidad activa y muchas credenciales.
- **IntentoVerificacion**: una evaluación de prueba de vida + comparación selfie vs. documento.
  Atributos: identificador, pasajero, referencia a la imagen, resultado, motivo de fallo, score de
  prueba de vida, score de comparación, umbrales, proveedor, fecha.
- **IdentidadDigital**: identidad verificada del pasajero. Atributos: identificador, pasajero,
  intento de origen, estado (`ACTIVA`, `REVOCADA`), fecha de creación.
- **CredencialAcceso**: pase QR de corta vida. Atributos: identificador, pasajero, identidad,
  código de vuelo, permisos, firma, fecha de emisión, fecha de expiración, estado actual.
- **TransicionCredencial**: registro inmutable de un cambio de estado (o intento rechazado).
  Atributos: credencial, estado anterior, estado solicitado, aceptada/rechazada, motivo, fecha.
- **TokenUsoUnico**: entrada de vida corta en el almacén rápido que indica si la credencial ya fue
  usada; caduca junto con la credencial.
- **EventoPendiente**: evento de dominio (`credencial.emitida`) con identificador, tipo, versión,
  contenido, estado de entrega e intentos, que garantiza que no se pierda.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: Un pasajero completa registro + verificación biométrica exitosa en menos de 2 minutos
  de interacción.
- **SC-002**: El 95 % de las solicitudes de pase entregan el QR al pasajero en menos de 1 segundo.
- **SC-003**: El 100 % de las credenciales emitidas tienen expiración entre 30 y 60 segundos y
  firma verificable.
- **SC-004**: 0 credenciales consumidas más de una vez en pruebas de concurrencia (RN-06).
- **SC-005**: 0 imágenes de selfie almacenadas en la base relacional; el 100 % de los intentos con
  imagen tienen referencia recuperable.
- **SC-006**: El 100 % de las identidades creadas generan su evento `credencial.emitida`, incluso
  si el canal de eventos estuvo caído durante la creación (entrega en menos de 5 minutos tras su
  recuperación).
- **SC-007**: Cuando el proveedor biométrico falla, el pasajero recibe respuesta en menos de 10
  segundos en el 99 % de los casos.
- **SC-008**: Con el pase abierto, el pasajero siempre ve un QR vigente: en el 99 % de las
  renovaciones el nuevo QR está disponible antes de que venza el anterior.

## Assumptions

- El stack está fijado por la constitución: la base relacional es Neon Postgres, el almacén de
  medios es Vercel Blob, el almacén rápido es Upstash Redis y el canal de eventos es Upstash
  QStash; la autenticación la provee Clerk.
- La lectura del documento (OCR/MRZ) ocurre en el cliente o en un paso previo; este spec recibe
  los datos ya extraídos junto con la foto del rostro recortada del documento. La lectura por chip
  NFC queda para una versión posterior.
- El proveedor biométrico (prueba de vida + comparación) se trata como externo e intercambiable
  (incluido un mock); ambos umbrales tienen valor por defecto 0,80 y son configurables por separado.
  No se confía en resultados declarados por el cliente.
- Máximo 3 intentos fallidos antes de `REQUIERE_REVISION_MANUAL`; la gestión de esos casos
  (consola del agente humano) está fuera de alcance.
- El código de vuelo se recibe en la solicitud y solo se valida su formato; la verificación contra
  la aerolínea/GDS está fuera de alcance. Los permisos se asignan con un valor por defecto
  (`embarque`) hasta que exista integración de vuelos.
- La transición a `CONSUMIDA` la ejecutará el módulo de checkpoint (fuera de alcance); este spec
  define y protege la regla, no la validación en puerta.
- La expiración de credenciales se refleja al consultarla (o por un proceso periódico); no se
  requiere notificación en tiempo real al pasajero.
- Retención: las selfies y la foto del documento se conservan mientras la identidad esté activa y un máximo de 90 días
  tras su revocación o tras el último intento fallido; historiales de credenciales se conservan
  12 meses. Estos plazos deben confirmarse con el equipo legal (protección de datos biométricos).
- Observabilidad (métricas, trazas) queda a cargo de otro módulo; este spec solo exige no impedir
  su instrumentación.

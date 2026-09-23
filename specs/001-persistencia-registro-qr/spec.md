# Feature Specification: Persistence of the registration, biometric verification and QR flow

**Feature Branch**: `001-persistencia-registro-qr`

**Created**: 2026-09-23

**Status**: Draft

**Input**: User description: "I need the data persistence for AeroPass's registration, biometric
verification and QR generation flow: (1) registration with the document data and an initial
status; (2) selfie in a media store and the comparison result (success/failure, score)
referencing the image; (3) creation of the IdentidadDigital and an asynchronous
`credencial.emitida` event; (4) CredencialAcceso (QR) with passenger, flight, permissions,
signature and a 30–60 s expiration, single-use token in a fast store and history in the
relational database; states EMITIDA, ACTIVA, CONSUMIDA, EXPIRADA, REVOCADA; RN-06 (cannot be
consumed twice). Out of scope: checkpoint validation, human agent console, observability."

## Clarifications

### Session 2026-09-23

- Q: Must the biometric verification include a liveness check in addition to the selfie vs.
  document comparison? → A: Yes, on the server: the provider evaluates liveness and comparison;
  the attempt stores both results and scores, and is `EXITOSO` only if both exceed their
  threshold.
- Q: Is the QR renewed automatically while the pass is open, or only on demand? → A: Automatic
  renewal before it expires; the previous credential becomes `REVOCADA`; limit of 30 issuances per
  passenger per minute.
- Q: Which image is the selfie compared against, if the document only provides text data? → A:
  At registration the client sends, together with the data, the face photo cropped from the
  document; it is stored in the private media store and the biometric comparison uses that photo
  as the reference. (Default decision taken in the consistency analysis; see `/speckit-analyze`,
  finding U1.)

## User Scenarios & Testing *(mandatory)*

### User Story 1 - Identity document registration (Priority: P1)

An authenticated passenger scans their identity document. The system stores the data read (full
name, document type and number, expiration date) associated with their passenger record, which
starts in the initial status `PENDIENTE_VERIFICACION`. The document's face photo is stored in the
private media store and the record only keeps its reference.

**Why this priority**: it is the entry point of the whole flow; without a registered passenger
there is no verification, identity or QR.

**Independent Test**: send the data of a valid document and check that a retrievable passenger
exists with that data and status `PENDIENTE_VERIFICACION`.

**Acceptance Scenarios**:

1. **Given** an authenticated passenger with no previous registration, **When** they send the data
   and photo of a valid document, **Then** a passenger is created with that data, the photo
   reference and status `PENDIENTE_VERIFICACION`, and the response returns its identifier.
2. **Given** a document with an expiration date before today, **When** it is sent, **Then** the
   registration is rejected with an explicit reason ("expired document") and nothing is persisted.
3. **Given** a passenger already registered with the same document type and number in the same
   account, **When** they send the document again, **Then** no duplicate is created and the
   existing passenger is returned with its current status.
4. **Given** a document type and number already associated with another account, **When**
   registration is attempted, **Then** it is rejected with reason "document already registered".
5. **Given** an account that already registered a document, **When** it tries to register a
   different document, **Then** it is rejected (one account corresponds to a single passenger).

---

### User Story 2 - Biometric verification with a selfie (Priority: P1)

The registered passenger takes a selfie. The image is stored in the media store (never in the
relational database) and the result of comparing it against the document photo (success/failure
and confidence score) is saved as a verification attempt that references the image location.

**Why this priority**: without a successful verification there can be no digital identity; it is
the product's central security control.

**Independent Test**: with a passenger in `PENDIENTE_VERIFICACION`, send a selfie and check that
the image is retrievable from its reference, that an attempt exists with result and score, and
that the passenger changes status according to the result.

**Acceptance Scenarios**:

1. **Given** a `PENDIENTE_VERIFICACION` passenger, **When** they send a selfie whose liveness and
   comparison exceed their thresholds, **Then** the image is stored, an `EXITOSO` attempt is
   recorded with both scores and the image reference, and the passenger moves to `VERIFICADO`.
2. **Given** a `PENDIENTE_VERIFICACION` passenger, **When** liveness or comparison does not exceed
   its threshold (e.g. a photo of a screen), **Then** a `FALLIDO` attempt is recorded with both
   scores and the reason (`LIVENESS` or `COMPARACION`), and the passenger stays in
   `PENDIENTE_VERIFICACION` as long as attempts remain.
3. **Given** a passenger who accumulates 3 `FALLIDO` attempts, **When** the third is recorded,
   **Then** the passenger moves to `REQUIERE_REVISION_MANUAL` and accepts no more selfies.
4. **Given** that the comparison provider does not respond or is degraded, **When** the passenger
   sends a selfie, **Then** a `NO_CONCLUYENTE` attempt is recorded that does not count as a
   failure and the passenger is told to retry later, without being left blocked waiting.
5. **Given** that the image could not be stored, **When** the passenger sends the selfie, **Then**
   no comparison is performed and no attempt with an invalid reference is recorded.

---

### User Story 3 - Digital identity creation (Priority: P2)

After a successful verification, the system automatically creates the passenger's
IdentidadDigital and publishes the `credencial.emitida` event so that other services (audit,
escalation) react without the passenger waiting for them.

**Why this priority**: it enables QR issuance and integration with other teams' modules, but
depends on stories 1 and 2.

**Independent Test**: trigger a successful verification and check that exactly one active
IdentidadDigital exists for the passenger and that the event was delivered (or left pending
delivery) with the expected data.

**Acceptance Scenarios**:

1. **Given** an `EXITOSO` attempt, **When** it completes, **Then** an `ACTIVA` IdentidadDigital is
   created linked to the passenger and to the attempt that originated it, and the response to the
   passenger does not wait for the event delivery.
2. **Given** that the event channel is unavailable, **When** the identity is created, **Then** the
   identity is created anyway and the event is left pending and retried until delivered.
3. **Given** a passenger who already has an `ACTIVA` IdentidadDigital, **When** another successful
   verification occurs, **Then** a second active identity is not created.

---

### User Story 4 - Dynamic QR generation (CredencialAcceso) (Priority: P2)

A passenger with an active IdentidadDigital requests their pass for a flight. The system builds a
CredencialAcceso with their identifier, flight code, permissions, digital signature and an
expiration of 30 to 60 seconds; it registers the single-use token for fast verification and
stores the credential's metadata and state history.

**Why this priority**: it is the deliverable visible to the passenger, but requires a prior
digital identity.

**Independent Test**: with an active identity, request a pass and check that the token is
retrievable as unused in the fast store, that it expires within the defined time, that its
signature is verifiable and that its history exists with the states `EMITIDA` → `ACTIVA`.

**Acceptance Scenarios**:

1. **Given** a passenger with an `ACTIVA` IdentidadDigital, **When** they request a pass for a
   valid flight code, **Then** a signed credential is issued with an expiration between 30 and
   60 s, its token is registered as unused and its history shows `EMITIDA` and then `ACTIVA`.
2. **Given** a passenger without an active IdentidadDigital, **When** they request a pass, **Then**
   it is rejected and no credential is created.
3. **Given** an `ACTIVA` credential for the same passenger and flight that is about to expire,
   **When** the app automatically requests renewal, **Then** the previous one becomes `REVOCADA`
   (reason `RENOVACION`), its token stops being acceptable and a new one is issued.
4. **Given** an `ACTIVA` credential whose time ran out without being consumed, **When** it is
   queried, **Then** it shows as `EXPIRADA` and its token is no longer acceptable.
5. **Given** a `CONSUMIDA` credential, **When** any component tries to consume it again, **Then**
   the transition is rejected (RN-06) and the attempt is recorded in the history.
6. **Given** a passenger who already obtained 30 credentials in the last minute, **When** they
   request another, **Then** the request is rejected with an indication to retry later and no
   credential is created.

---

### Edge Cases

- Incomplete or unreadable document data (missing name, number, date or photo): the registration
  is rejected, indicating the missing fields.
- Selfie or document photo with an unsupported format or larger than 4 MB (body limit of the
  deployment platform): rejected before storing it.
- The document photo could not be stored: the registration is rejected with an indication to
  retry and the passenger is not persisted.
- The passenger's status changes while their selfie is being evaluated (e.g. two selfies sent at
  once): only the first recorded result counts; the second is rejected and its image is left
  orphaned and identifiable.
- Selfie sent by a passenger already `VERIFICADO` or `REQUIERE_REVISION_MANUAL`: rejected.
- Image stored but failure when recording the attempt: the orphaned image stays identifiable for
  later cleanup and does not count as a verification.
- Document that expires between registration and the pass request: pass issuance is rejected.
- Two simultaneous pass requests for the same passenger and flight: only one credential stays
  `ACTIVA`; the other becomes `REVOCADA`.
- Service cold start: no data in the flow depends on in-memory state; everything is recovered
  from the persistent stores.
- Invalid transition on a credential (e.g. `EXPIRADA` → `ACTIVA`, `REVOCADA` → `CONSUMIDA`):
  rejected and recorded.

## Requirements *(mandatory)*

### Functional Requirements

**Registration**

- **FR-001**: The system MUST persist, per passenger, full name, document type, document number,
  expiration date, owning authenticated account, status and creation and update dates.
- **FR-001a**: The system MUST receive the document's face photo at registration, store it in the
  private media store and persist only its reference; it is the reference image for the
  biometric comparison.
- **FR-002**: The system MUST assign the initial status `PENDIENTE_VERIFICACION` to every new
  passenger.
- **FR-003**: The system MUST reject expired documents or documents with missing mandatory
  fields, without persisting partial data.
- **FR-004**: The system MUST guarantee uniqueness by document type + number; a resubmission from
  the same account returns the existing passenger and from another account it is rejected. Each
  account corresponds to a single passenger: registering a different document from an already
  registered account is rejected.

**Biometric verification**

- **FR-005**: The system MUST store each selfie in the media store and MUST NOT save the image
  content in the relational database.
- **FR-006**: The system MUST record each verification attempt with: passenger, image reference,
  result (`EXITOSO`, `FALLIDO`, `NO_CONCLUYENTE`), failure reason (`LIVENESS`, `COMPARACION` or
  none), liveness score and comparison score (0–1, when available), thresholds applied, provider
  that evaluated it and date.
- **FR-007**: The system MUST evaluate on the server both liveness (the selfie corresponds to a
  real, present person) and the comparison of the selfie against the document photo (FR-001a),
  and MUST consider an attempt successful only if both scores are equal to or above their
  configurable thresholds.
- **FR-008**: The system MUST move the passenger to `VERIFICADO` after a successful attempt and to
  `REQUIERE_REVISION_MANUAL` on the third `FALLIDO` attempt; `NO_CONCLUYENTE` attempts do not
  count.
- **FR-009**: When the comparison provider fails or exceeds the maximum wait time, the system
  MUST respond with a `NO_CONCLUYENTE` result instead of blocking.
- **FR-010**: Stored images MUST NOT be publicly accessible; only the system can retrieve them
  through the persisted reference.

**Digital identity**

- **FR-011**: After a successful attempt, the system MUST create an `ACTIVA` IdentidadDigital that
  references the passenger and the attempt that originated it; a passenger MUST have at most one
  `ACTIVA` identity.
- **FR-012**: The system MUST publish the `credencial.emitida` event asynchronously after
  creating the identity, without the response to the passenger waiting for its delivery.
- **FR-013**: The system MUST guarantee that the event is not lost if the channel is unavailable:
  it is recorded as pending and retried until delivered; each event carries a unique identifier
  so consumers process it only once.
- **FR-014**: The event content MUST include event identifier, schema version, type, passenger
  identifier, identity identifier and date, and MUST NOT include document data or the image.

**Access credential (QR)**

- **FR-015**: The system MUST issue credentials only to passengers with an `ACTIVA`
  IdentidadDigital and a valid document.
- **FR-016**: Each credential MUST contain passenger identifier, flight code, permissions,
  verifiable digital signature and an expiration date between 30 and 60 seconds (configurable
  within that range) from issuance; a credential missing any of these elements MUST NOT be
  issued.
- **FR-017**: The system MUST register the single-use token in the fast store with an expiry
  equal to the credential's, marked as unused.
- **FR-018**: The system MUST persist the metadata of each credential and each state transition
  (previous state, new state, date, reason) as an immutable history.
- **FR-019**: The system MUST allow only these transitions: `EMITIDA`→`ACTIVA`,
  `EMITIDA`→`REVOCADA`, `EMITIDA`→`EXPIRADA`, `ACTIVA`→`CONSUMIDA`, `ACTIVA`→`EXPIRADA`,
  `ACTIVA`→`REVOCADA`. `CONSUMIDA`, `EXPIRADA` and `REVOCADA` are final; any other transition,
  including `CONSUMIDA`→`CONSUMIDA` (RN-06), MUST be rejected and recorded.
- **FR-020**: The system MUST support automatic QR renewal while the pass is open: when issuing a
  new credential for the same passenger and flight, it MUST revoke the previous non-final
  credential (reason `RENOVACION`) and remove its token's validity, so that at any time there is
  at most one `ACTIVA` credential per passenger and flight.
- **FR-020a**: The system MUST limit issuance to 30 credentials per passenger per minute and
  reject requests exceeding that limit without creating a credential.
- **FR-021**: The system MUST expose the credential consumption operation (enforcing RN-06) as an
  extension point for the checkpoint module, even though checkpoint validation is out of this
  scope.

**Cross-cutting**

- **FR-022**: Every operation MUST require an authenticated passenger and MUST restrict access to
  the passenger's own data.
- **FR-023**: No operation MUST depend on state kept in memory between requests.

### Key Entities

- **Pasajero**: a person registered in AeroPass. Attributes: identifier, authenticated account,
  full name, document type and number, document expiration date, reference to the document
  photo, status (`PENDIENTE_VERIFICACION`, `VERIFICADO`, `REQUIERE_REVISION_MANUAL`), dates. Has
  many verification attempts, at most one active identity and many credentials.
- **IntentoVerificacion**: one liveness + selfie vs. document comparison evaluation. Attributes:
  identifier, passenger, image reference, result, failure reason, liveness score, comparison
  score, thresholds, provider, date.
- **IdentidadDigital**: the passenger's verified identity. Attributes: identifier, passenger,
  originating attempt, status (`ACTIVA`, `REVOCADA`), creation date.
- **CredencialAcceso**: short-lived QR pass. Attributes: identifier, passenger, identity, flight
  code, permissions, signature, issue date, expiration date, current status.
- **TransicionCredencial**: immutable record of a state change (or a rejected attempt).
  Attributes: credential, previous state, requested state, accepted/rejected, reason, date.
- **TokenUsoUnico**: short-lived entry in the fast store indicating whether the credential was
  already used; it expires together with the credential.
- **EventoPendiente**: domain event (`credencial.emitida`) with identifier, type, version,
  content, delivery status and attempts, which guarantees it is not lost.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: A passenger completes registration + successful biometric verification in less
  than 2 minutes of interaction.
- **SC-002**: 95 % of pass requests deliver the QR to the passenger in less than 1 second.
- **SC-003**: 100 % of issued credentials have an expiration between 30 and 60 seconds and a
  verifiable signature.
- **SC-004**: 0 credentials consumed more than once in concurrency tests (RN-06).
- **SC-005**: 0 selfie images stored in the relational database; 100 % of attempts with an image
  have a retrievable reference.
- **SC-006**: 100 % of created identities generate their `credencial.emitida` event, even if the
  event channel was down during creation (delivery in less than 5 minutes after it recovers).
- **SC-007**: When the biometric provider fails, the passenger gets a response in less than 10
  seconds in 99 % of cases.
- **SC-008**: With the pass open, the passenger always sees a valid QR: in 99 % of renewals the
  new QR is available before the previous one expires.

## Assumptions

- The stack is fixed by the constitution: the relational database is Neon Postgres, the media
  store is Vercel Blob, the fast store is Upstash Redis and the event channel is Upstash QStash;
  authentication is provided by Clerk.
- Document reading (OCR/MRZ) happens on the client or in a previous step; this spec receives the
  already extracted data together with the face photo cropped from the document. NFC chip reading
  is left for a later version.
- The biometric provider (liveness + comparison) is treated as external and interchangeable
  (including a mock); both thresholds default to 0.80 and are configurable separately. Results
  declared by the client are not trusted.
- Maximum of 3 failed attempts before `REQUIERE_REVISION_MANUAL`; handling those cases (human
  agent console) is out of scope.
- The flight code is received in the request and only its format is validated; verification
  against the airline/GDS is out of scope. Permissions are assigned a default value (`embarque`)
  until a flights integration exists.
- The transition to `CONSUMIDA` will be executed by the checkpoint module (out of scope); this
  spec defines and protects the rule, not the gate validation.
- Credential expiration is reflected when queried (or by a periodic process); no real-time
  notification to the passenger is required.
- Retention: selfies and the document photo are kept while the identity is active and for at most
  90 days after its revocation or after the last failed attempt; credential histories are kept
  for 12 months. These periods must be confirmed with the legal team (biometric data protection).
- Observability (metrics, traces) is handled by another module; this spec only requires not
  preventing its instrumentation.

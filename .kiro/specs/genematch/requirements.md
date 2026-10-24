# Requirements Document

## Introduction

GeneMatch lets two partners learn whether they both carry a harmful variant in the same autosomal-recessive gene, which would give each pregnancy a 25% chance of an affected child, without revealing their carrier status to each other, to a server, or to an AI model beyond the agreed result. A mock sequencing lab issues signed carrier credentials. The partners run a Diffie-Hellman-style private set intersection over lab-issued gene tokens through an untrusted relay, using commit-then-reveal. A counselor agent explains the result. The same credential is reused for a second use case: proving clinical-trial eligibility by disclosing exactly one lab-signed finding.

## Glossary
- **Carrier gene**: a gene in which a person carries one pathogenic variant. Matching is at the gene level, because two different pathogenic variants in the same gene can still produce an affected child.
- **Panel**: the fixed list of autosomal-recessive genes the lab tests for (`data/panel.json`).
- **Lab token**: `T_g = k_lab · H(g)`, a ristretto255 point only the lab can compute.
- **Credential**: a lab-signed bundle of one person's lab tokens plus metadata.
- **Relay**: an untrusted server that forwards messages between the two wallets.
- **Wallet**: the per-user client that holds the credential and the session secret.
- **Detail mode / Count mode**: result modes revealing the shared conditions, or only how many there are.
- **Signed claim**: one genetic finding (for example a germline BRCA1 pathogenic variant) signed individually by the lab, so it can be shown on its own.
- **Holder key**: an Ed25519 key pair held only by the wallet (generated on its behalf by the seed script in the demo); its public key is embedded in every signed claim so only the holder can present it.
- **Trial verifier**: a page run by a trial site that requests one claim and checks it.

## Requirements

### Requirement 1: Lab-certified carrier credentials

**User Story:** As a tested person, I want my carrier results issued as a signed credential, so that my partner can trust them without seeing them.

#### Acceptance Criteria
1. WHEN the lab issues a credential THE SYSTEM SHALL compute one lab token `T_g = k_lab · H(g)` for each carrier gene `g` of that person.
2. THE SYSTEM SHALL include in each credential the person ID, token list, token count, a commitment `SHA-256("genematch/v1/commit/" || salt || sorted tokens)` (salt is 16 random bytes when the commitment-salt flag is on, empty when off), issue time, and an Ed25519 signature by the lab over the canonical encoding of the person ID, count, commitment, and issue time; the signature covers the tokens through the commitment.
3. IF a requested gene is not in the panel or is not autosomal recessive THEN THE SYSTEM SHALL refuse to issue the credential.
4. THE SYSTEM SHALL keep `k_lab` and the lab signing key inside the lab service and SHALL publish only the lab's Ed25519 verification key.
5. THE SYSTEM SHALL cap the token count per credential at `MAX_SET` (default 20).

### Requirement 2: Session pairing through an untrusted relay

**User Story:** As a partner, I want to start a check and invite my partner with a code, so that we can run it from separate devices, even at different times.

#### Acceptance Criteria
1. WHEN a user starts a check THE SYSTEM SHALL create a session with a random 128-bit session ID and a single-use invite code that expires after 24 hours.
2. WHEN a second user joins with a valid invite code THE SYSTEM SHALL bind exactly two participants to the session and SHALL reject any further join attempts.
3. THE relay SHALL store and forward only commitments, blinded points, counts, and lab signatures, and SHALL never receive gene IDs, lab tokens, or session secrets.
4. WHILE one participant is waiting for the other THE wallet SHALL show a waiting state and SHALL resume the protocol automatically when the partner arrives.
5. IF a session does not complete within 24 hours THEN THE relay SHALL delete all session data.

### Requirement 3: Result mode consent

**User Story:** As a partner, I want to agree in advance on what we will learn, so that nothing is revealed that I did not consent to.

#### Acceptance Criteria
1. WHEN creating a session THE initiator SHALL choose Count mode or Detail mode.
2. WHEN joining THE wallet SHALL display the chosen mode in plain language and SHALL send no protocol data until the joiner accepts.
3. IF the joiner declines THEN THE SYSTEM SHALL close the session with no data exchanged.
4. THE SYSTEM SHALL give both participants the same output.

### Requirement 4: Commit-then-reveal

**User Story:** As a partner, I want both of us locked into our inputs before either sees anything, so that neither of us can adapt after peeking.

#### Acceptance Criteria
1. BEFORE sending its blinded set (round 1) THE wallet SHALL send `SHA-256("genematch/v1/blinded/" || canonical blinded set)` to the relay (round 0).
2. THE relay SHALL release either participant's blinded set only after both commitments are received.
3. IF a revealed blinded set does not match its commitment THEN THE wallet SHALL abort with an integrity error shown to the user.

### Requirement 5: Private set intersection

**User Story:** As a partner, I want our carrier genes compared without either of us seeing the other's list, so that only the agreed result is learned.

#### Acceptance Criteria
1. WHEN a session starts THE wallet SHALL sample a fresh secret scalar and SHALL never transmit or persist it.
2. WHEN sending round 1 THE wallet SHALL send its blinded set `{a · T_g}` in random order, together with its credential count, commitment, issue time, person ID, and lab signature.
3. IF the partner's lab signature is invalid, the partner's blinded set size differs from its signed count, or the count exceeds `MAX_SET` THEN THE wallet SHALL abort the protocol.
4. IF any received point fails ristretto255 validation or is the identity THEN THE wallet SHALL abort the protocol.
5. WHEN the partner's blinded set arrives THE wallet SHALL return `{a · X}` for each received point `X`, preserving order in Detail mode and shuffling in Count mode.
6. THE wallet SHALL compute the intersection locally, and THE relay SHALL never learn the result.
7. WHEN the protocol completes THE wallet SHALL delete the session secret from memory.

### Requirement 6: Counselor agent

**User Story:** As a partner, I want the result explained in plain language with next steps, so that I know what it means and what to do.

#### Acceptance Criteria
1. WHEN a result is available THE agent SHALL explain it using only facts from the curated panel entries for the shared genes (condition name, inheritance, a one-line description, a MedlinePlus link).
2. WHILE in Count mode THE agent SHALL receive only the shared count, never gene or condition names.
3. WHEN at least one gene is shared THE agent SHALL state that each pregnancy has a 25% chance of an affected child, recommend a genetic counselor, and mention that options exist without recommending any.
4. WHEN no gene is shared THE agent SHALL state that the risk for the tested conditions is greatly reduced but not zero, and that untested conditions are not covered.
5. THE agent SHALL never present its output as a diagnosis and SHALL include a short disclaimer.
6. THE agent SHALL generate 3–5 questions for the couple to ask a genetic counselor.
7. IF the LLM is unavailable THEN THE agent SHALL fall back to a deterministic template that meets criteria 3–6.

### Requirement 7: Demo and transparency

**User Story:** As a judge, I want to see what each party actually saw, so that I can verify the privacy claims.

#### Acceptance Criteria
1. THE wallet SHALL offer a "What did everyone see?" panel listing, per party (Alex, Sam, relay, agent), the data it received, with points shown as 8-character hex prefixes.
2. THE seed script SHALL create three demo couples: one shared carrier gene, no shared genes, and two shared genes.
3. WHEN run locally THE SYSTEM SHALL complete a full check in under 3 seconds of compute, excluding the LLM call.
4. THE UI SHALL display a persistent banner: "Demo with synthetic data. Not medical advice."

### Requirement 8: Clinical trial eligibility by selective disclosure

**User Story:** As a patient, I want to prove I have the genetic finding a trial requires, so that I can be screened without handing over my genome or my other results.

#### Acceptance Criteria
1. WHEN the lab issues a credential THE SYSTEM SHALL also issue a list of signed claims, one per finding, each containing person ID, gene, finding, a random 128-bit salt, the holder public key, issue time, and an Ed25519 lab signature over the canonical encoding of these fields.
2. WHEN a trial verifier requests eligibility THE verifier SHALL send the trial ID, the required gene and finding, and a fresh random nonce.
3. WHEN the user approves the request THE wallet SHALL send exactly one claim matching the requested gene, plus a holder signature over `canon({claim_hash, nonce, trial_id})`, and SHALL send no other claims, tokens, or credential fields.
4. IF the wallet holds no matching claim THEN THE wallet SHALL tell the user and send nothing.
5. WHEN a presentation arrives THE verifier SHALL check the lab signature, the holder signature against the holder key in the claim, the nonce, and that the claim matches the requirement, and SHALL show "Eligible — verified by lab" or a specific failure reason.
6. IF the nonce was already used or has expired (5 minutes) THEN THE verifier SHALL reject the presentation.
7. THE transparency panel SHALL show that the trial received exactly one claim.

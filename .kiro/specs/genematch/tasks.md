# Implementation Plan

Time boxes assume a 9:00 start, with a feature freeze at 17:00 and submission by 20:00.

- [ ] 1. Project skeleton and crypto core (9:00–10:30)
  - [ ] 1.1 Create the repository layout from `structure.md`, plus `pyproject.toml` with fastapi, uvicorn, rbcl, pynacl>=1.6, pydantic, httpx, and pytest
  - [ ] 1.2 Verify that `rbcl` installs and exposes the ristretto255 bindings (`from_hash`, `scalarmult`, `scalar_random`, `is_valid_point`); if not, switch to `pysodium`, then to `openmined-psi` as a last resort
  - [ ] 1.3 Implement `core/crypto.py`: `hash_to_group`, `random_scalar`, `mul`, `blind_set`, `commit_tokens`, `commit_blinded`, `canon`
  - [ ] 1.4 Write unit tests for commutativity, domain separation, invalid and identity point rejection, and commitment determinism
  - _Requirements: 1.1, 1.2, 5.1, 5.4_

- [ ] 2. Gene panel and lab service (10:30–11:15)
  - [ ] 2.1 Write `data/panel.json` with 30–60 autosomal-recessive genes (for example CFTR, HBB, HEXA, SMN1, GBA, PAH, ASPA, FANCC), each with a condition, a one-line summary, and a MedlinePlus link
  - [ ] 2.2 Implement `lab/service.py`: key generation, `POST /credentials`, `GET /pubkey`, the `MAX_SET` cap, and the panel and inheritance check
  - [ ] 2.3 Write `scripts/seed_demo.py` to issue credentials for the three demo couples (one shared gene, none shared, two shared)
  - _Requirements: 1.1–1.5, 7.2_

- [ ] 3. Protocol engine, terminal-only (11:15–12:30) — **milestone: PSI works end to end in pytest**
  - [ ] 3.1 Implement `core/protocol.py`: `Session`, rounds 0–2, `verify_peer`, and `result` for both modes
  - [ ] 3.2 Write protocol tests for all three couples in both modes
  - [ ] 3.3 Write adversarial tests: inflated set, forged token, commitment mismatch, bad signature
  - _Requirements: 4.1–4.3, 5.1–5.7, 3.4_

- [ ] 4. Relay service (13:15–14:00)
  - [ ] 4.1 Implement sessions, invite codes, join (rejecting a third participant), accept and decline, and 24-hour expiry
  - [ ] 4.2 Implement round storage with commit-then-reveal release rules
  - [ ] 4.3 Implement the transparency log (hex prefixes only) at `GET /sessions/{id}/log`
  - _Requirements: 2.1–2.5, 3.1–3.3, 4.2, 7.1_

- [ ] 5. Wallet service and UI (14:00–15:30) — **milestone: two-browser demo**
  - [ ] 5.1 Wallet endpoints: start, join, status, and seen; the relay polling loop; scalar zeroing on completion or abort
  - [ ] 5.2 UI screens: home (credential summary), start check (mode choice and invite code), join (mode explanation, then accept), waiting, result
  - [ ] 5.3 Build the "What did everyone see?" panel and the synthetic-data banner
  - [ ] 5.4 Write `scripts/run_demo.sh` to start the lab, relay, and both wallets
  - _Requirements: 2.4, 3.1–3.3, 5.6, 7.1, 7.4_

- [ ] 6. Counselor agent (15:30–16:30)
  - [ ] 6.1 Write the Bedrock adapter and system prompt, with a deterministic template fallback
  - [ ] 6.2 Implement `explain()` for both modes, plus counselor questions
  - [ ] 6.3 Write tests: the Count-mode prompt contains no gene names, and the fallback meets all required statements
  - _Requirements: 6.1–6.7_

- [ ] 7. Trial eligibility (16:30–17:15, cut first if behind)
  - [ ] 7.1 Wallet holder key: generate at setup, send the public key to the lab
  - [ ] 7.2 Lab: issue per-finding signed claims with salt and holder public key; extend the seed script so demo people have BRCA1 or BRCA2 findings
  - [ ] 7.3 Write `data/trials.json` with two demo trials
  - [ ] 7.4 Implement `trial/service.py`: requests with nonces, presentation verification, verifier page
  - [ ] 7.5 Wallet: `POST /present` with an approval prompt that names the single finding shared
  - [ ] 7.6 Tests: valid presentation, tampered claim, wrong holder key, reused nonce, expired nonce, wrong gene
  - [ ] 7.7 Add "Trial saw: 1 claim" to the transparency panel
  - _Requirements: 8.1–8.7_

- [ ] 8. Polish and stretch (17:15–17:30, cut freely)
  - [ ] 8.1 Optional: deploy the relay to AWS (App Runner or Lambda)
  - [ ] 8.2 Optional: design notes for S1, S2, and S3 in the README
  - [ ] 8.3 Check timing: a full check takes under 3 seconds of compute
  - _Requirements: 7.3_

- [ ] 9. Submission (17:30–18:30)
  - [ ] 9.1 README: the two-line pitch, a demo GIF in the first screen, how to run, the threat model table, residual risks, and prior art (Baldi et al. 2011; authorized PSI)
  - [ ] 9.2 Record a 60–90 second demo video: two windows, a shared-carrier result, the transparency panel, the counselor, then the same credential proving trial eligibility
  - [ ] 9.3 Confirm `.kiro/` is committed, push to GitHub, verify on GitHub, and submit on SundAI with the track set to Biotech & Healthcare

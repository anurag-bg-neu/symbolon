# Implementation Plan

Re-planned at 11:52 with no code written yet (the original plan assumed a 9:00 start). Three people work in parallel from 12:00. Feature freeze at 18:00, submitted by 19:30, hard deadline 20:00.

## Owners

| Tag | Role | Owns (folders) |
|---|---|---|
| **P1** | Crypto and protocol | `core/` (including `core/issuance.py`), `tests/test_crypto*.py`, `tests/test_protocol*.py`, claim signing and verification logic |
| **P2** | Backend services | `relay/`, `lab/`, `wallet/service.py`, `trial/service.py`, `scripts/` |
| **P3** | Frontend, agent, story | `data/`, `agent/`, `wallet/static/`, `trial/static/`, `README.md`, demo video |

Stay inside your folders. Shared files (`core/models.py`, `pyproject.toml`) change only after a heads-up in chat.

## Timeline

| Time | P1 (crypto) | P2 (backend) | P3 (frontend, agent, story) |
|---|---|---|---|
| 12:00–12:20 | 1.1, 1.2: skeleton, `pyproject.toml`, rbcl check, `core/models.py`; push | Read design.md relay section | 2.1: start `data/panel.json` |
| 12:20–13:15 | 1.3, 1.4: crypto core and tests | 4.1–4.3: relay (payloads are opaque, so no crypto needed) | 2.1: finish panel; push by 12:45. Then 6.1: template fallback |
| 13:15–14:00 | 3.1, 3.2: protocol and couple tests (fixture lab key in `tests/conftest.py`) | 2.2, 2.3: lab keys, issuance, lab service, seed script | 6.1–6.3: Bedrock adapter, `explain()`, tests |
| **14:00** | **Milestone A: PSI end to end in pytest** | | |
| 14:00–14:45 | 3.3: adversarial tests | 5.1: wallet endpoints and relay polling | 5.2: UI screens against mocks of the design.md wallet API |
| 14:45–16:00 | Pair with P2 on 5.1: `Session` in the wallet, scalar zeroing, error messages | 5.1 continued, 5.4: `run_demo.sh` | 5.2, 5.3: screens, transparency panel, banner |
| 16:00–16:30 | Integration and bug fixing, all three | | |
| **16:30** | **Milestone B: two-browser demo. Gate: if not green, cut Task 7 and everyone polishes the couple flow** | | |
| 16:30–18:00 | 7.2 (claim signing in lab), presentation verify in `core/`, 7.6 tests | 7.1, 7.4, 7.5: holder key, trial service, wallet `/present` | 7.3, 7.7: `trials.json`, verifier page, "Trial saw: 1 claim" |
| **18:00** | **Feature freeze** | | |
| 18:00–19:15 | 8.2, 8.3: S1–S3 notes, threat model table, timing check | 8.1 (optional deploy), fresh-clone install test | 9.1, 9.2: README, demo GIF, 60–90 s video |
| 19:15–19:30 | 9.3 all: push, verify on GitHub, submit on SundAI | | |

## Handoffs (the producer pushes by this time)

All interfaces are fixed in `design.md` ("Components and interfaces" and "Data models"), so nobody waits on a conversation. A handoff only means the real code replaces your mock.

| Time | From → To | What |
|---|---|---|
| 12:20 | P1 → all | Repo skeleton, `pyproject.toml`, `core/models.py` with every model in design.md |
| 12:45 | P3 → P2 | `data/panel.json` |
| 13:15 | P1 → P2 | `core/crypto.py` and `core/issuance.py` |
| 14:00 | P2 → P1, P3 | `var/wallets/*.json` from `seed_demo.py`; wallet endpoints returning the design.md shapes (P3 mocks them until then) |
| 16:30 | P1 → P2 | `core/claims.py` with `verify_presentation()` |

## Tasks

- [x] 1. Project skeleton and crypto core — **P1**, 12:00–13:15
  - [x] 1.1 Create the repository layout from `structure.md`, `pyproject.toml` with fastapi, uvicorn, rbcl, pynacl>=1.6, pydantic, httpx, boto3, and pytest, and `core/models.py` with every model in the design.md "Data models" table
    - _Requirements: none (setup)_
  - [x] 1.2 Verify that `rbcl` installs and exposes the ristretto255 bindings (`from_hash`, `scalarmult`, `scalar_random`, `is_valid_point`); if not, switch to `pysodium`, then to `openmined-psi` as a last resort
    - _Requirements: none (setup)_
  - [x] 1.3 Implement `core/crypto.py`: `hash_to_group`, `random_scalar`, `mul`, `blind_set`, `commit_tokens`, `commit_blinded`, `canon`, `sign`, `verify`, with the prefixes in design.md
    - _Requirements: 1.1, 1.2, 4.1, 5.1, 5.4_
  - [x] 1.4 Write unit tests for commutativity, domain separation, invalid and identity point rejection, commitment determinism, and signatures (valid, tampered, wrong key)
    - _Requirements: 1.1, 1.2, 4.3, 5.4_
  - [x] 1.5 Add the repository-local `crypto-rules-guard` hook and document its safeguards in the specification and `KIRO.md`
    - _Requirements: none (tooling)_
  - [x] 1.6 Add Hypothesis property-based tests for PSI correctness, DH commutativity, input-order independence, point validation, lab-key separation, and fresh wire blinding; document the properties and results in `design.md` and `KIRO.md`
    - _Requirements: 1.1, 1.4, 3.4, 5.1, 5.2, 5.4, 5.5, 5.6_

- [x] 2. Gene panel and lab service
  - [x] 2.1 **P3** (30 genes, every MedlinePlus page and AR inheritance statement confirmed), 12:00–12:45: write `data/panel.json` with 30–60 autosomal-recessive genes (for example CFTR, HBB, HEXA, SMN1, GBA, PAH, ASPA, FANCC; every gene used by the demo people must be included), each with a condition, a one-line summary, and a MedlinePlus Genetics condition link
    - _Requirements: 1.3, 6.1_
  - [x] 2.2 **P1**: `core/issuance.py` (`issue`, with panel, AR, duplicate, `MAX_SET`, and `CLAIM_GENES` checks, and the `salt_commitment` flag). **P2**, 13:15–13:45: `lab/keys.py` (`load_or_create`) and `lab/service.py` (`POST /credentials`, `GET /pubkey`), reading `GENEMATCH_COMMIT_SALT` and passing it to `issue`
    - _Requirements: 1.1, 1.2, 1.3, 1.4, 1.5_
  - [x] 2.3 **P2**, 13:45–14:00: write `scripts/seed_demo.py` (in-process, no HTTP) to issue credentials for the three demo couples in design.md and write `var/wallets/<person_id>.json`
    - _Requirements: 7.2_

- [x] 3. Protocol engine, terminal-only — **P1**, 13:15–14:45 — **milestone A at 14:00: PSI works end to end in pytest**
  - [x] 3.1 Implement `core/protocol.py`: `Session`, rounds 0–2, `verify_peer`, `result` for both modes, and `zero()`
    - _Requirements: 3.4, 4.1, 4.3, 5.1, 5.2, 5.3, 5.4, 5.5, 5.6, 5.7_
  - [x] 3.2 Write protocol tests for all three couples in both modes (fixture lab key in `tests/conftest.py`, using `core.issuance.issue`)
    - _Requirements: 3.4, 5.5, 5.6, 7.2_
  - [x] 3.3 Write adversarial tests: inflated set, commitment mismatch, bad signature, replayed round-1 message, reflection, cross-session splice, and mode split all abort; a forged token never matches (no abort, S1 limitation); a tampered own credential fails with `BAD_CREDENTIAL`
    - _Requirements: 4.3, 5.3, 5.4_

- [x] 4. Relay service — **P2**, 12:20–13:15
  - [x] 4.1 Implement sessions, invite codes, join (409 for a third participant), accept, decline, fail, state, and 24-hour deletion
    - _Requirements: 2.1, 2.2, 2.5, 3.3_
  - [x] 4.2 Implement round storage with the release rules in design.md
    - _Requirements: 2.3, 4.2_
  - [x] 4.3 Implement the transparency log (hex prefixes only) at `GET /sessions/{id}/log`
    - _Requirements: 7.1_

- [ ] 5. Wallet service and UI — 14:00–16:30 — **milestone B at 16:30: two-browser demo**
  - [x] 5.1 **P2** (P1 pairs from 14:45): wallet endpoints from the design.md table (`/me`, `/check/*`), the 500 ms relay polling loop, the in-process counselor call, and scalar zeroing on completion or abort. Construct `Session(role, mode, session_id, credential, genes, lab_pubkey)` and follow the wallet call sequence in design.md (`core/protocol.py` section)
    - _Requirements: 2.4, 3.2, 5.6, 5.7_
  - [x] 5.2 **P3** (built by P1): UI screens: home (credential summary from `/me`), start check (mode choice and invite code), join (mode explanation, then accept or decline), waiting, result with explanation
    - _Requirements: 2.4, 3.1, 3.2, 3.3_
  - [x] 5.3 **P3** (built by P1): build the "What did everyone see?" panel from `/check/seen`, and the synthetic-data banner
    - _Requirements: 7.1, 7.4_
  - [x] 5.4 **P2**: write `scripts/run_demo.sh` (macOS/Linux, Git Bash) and `scripts/run_demo.ps1` (Windows) to start the lab, relay, trial service, and both wallets for `COUPLE=1|2|3`
    - _Requirements: none (tooling)_

- [ ] 6. Counselor agent — **P3**, 12:45–14:00 (optional feature; template only for now)
  - [ ] 6.1 Write the deterministic template fallback first (done), then the Bedrock Converse adapter and system prompt (env `BEDROCK_MODEL_ID`, `AWS_REGION`, 10-second timeout). Bedrock is deferred by decision: only the marked `LLM HOOK` exists
    - _Requirements: 6.5, 6.7_
  - [x] 6.2 Implement `build_prompt()` and `explain()` for both modes, plus 3–5 counselor questions and output validation
    - _Requirements: 6.1, 6.2, 6.3, 6.4, 6.5, 6.6_
  - [x] 6.3 Write tests: the Count-mode prompt contains no gene or condition names, and the fallback meets all required statements in both the shared and none-shared cases
    - _Requirements: 6.2, 6.3, 6.4, 6.7_

- [x] 7. Trial eligibility — 16:30–18:00, **only if milestone B is green at 16:30**
  - [x] 7.1 **P2**: seed script generates one holder key per person, passes the public key to `issue`, and writes the seed only to that wallet's file
    - _Requirements: 8.1_
  - [x] 7.2 **P1**: per-finding signed claims in `core/issuance.py` (salt, holder public key, `genematch/v1/claim/` signature) and `core/claims.py` (`claim_hash`, `make_presentation`, `verify_presentation`)
    - _Requirements: 8.1, 8.3, 8.5_
  - [x] 7.3 **P3** (built by P1): write `data/trials.json` with two demo trials (one BRCA1, one BRCA2), in the design.md shape
    - _Requirements: 8.2_
  - [x] 7.4 **P2** (built by P1): implement `trial/service.py`: `/trials`, `/requests`, `GET /requests/{id}`, `/present`, the nonce store with 5-minute expiry, and serving the verifier page
    - _Requirements: 8.2, 8.5, 8.6_
  - [x] 7.5 **P2** (built by P1): wallet `GET /present/pending` and `POST /present`, following the trial request flow in design.md; tell the user and send nothing if no claim matches
    - _Requirements: 8.3, 8.4_
  - [x] 7.6 **P1**: tests: valid presentation, tampered claim, wrong holder key, reused nonce, expired nonce, wrong gene, and the wallet sends exactly one claim
    - _Requirements: 8.3, 8.5, 8.6_
  - [x] 7.7 **P3** (built by P1): verifier page (trial picker, wallet links, status polling), the wallet approval prompt naming the single finding, and "Trial saw: 1 claim" in the transparency panel
    - _Requirements: 8.3, 8.5, 8.7_

- [x] 10. Wallet UX upgrade — **P3** (built by P1); build in order, cut from the bottom
  - [x] 10.1 Demo accounts (one per port), sign-in, "Continue as <name>", wrong-wallet message, sign-out, protected routes; tests
    - _Requirements: 12.1–12.6_
  - [x] 10.2 `seed_demo.py` loads every wallet from `data/lab_records.json`, `--empty <person>` (repeatable), synthetic labels; tests
    - _Requirements: 9.2, 13.4_
  - [x] 10.3 Public landing page with five `<details>` sections and the Alex and Sam walkthrough
    - _Requirements: 11.1–11.3_
  - [x] 10.4 Plain-language check flow; "Privacy details" collapsed on the result screen; jargon test
    - _Requirements: 10.1–10.3_
  - [x] 10.5 Connect your lab (lab consent page, single-use collection, wallet-generated holder key); tests
    - _Requirements: 9.1, 9.2, 9.7_
  - [x] 10.6 Lab files: `core/labfile.py`, `make_lab_file.py` (lab-side tooling), wallet upload with confirmation and raw-DNA rejection; tests
    - _Requirements: 9.3–9.7_
  - [x] 10.8 Close the two lab weaknesses: patients sign in to the lab (patient-only consent and approval); "Download your results file" issues only the patient's own record; staff-only record editing; Origin check on lab POSTs; tests
    - _Requirements: 9.8–9.11_
  - [x] 10.7 Visual polish and motion (system fonts, one accent, reduced-motion, phone width)
    - _Requirements: 13.1–13.3_

- [ ] 8. Polish and stretch — 18:00–19:15, cut freely
  - [ ] 8.1 **P2**, optional: deploy the relay to AWS (App Runner or Lambda)
    - _Requirements: none (stretch)_
  - [ ] 8.2 **P1**, optional: design notes for S1, S2, and S3 in the README (written in design.md "Stretch designs"; copy into the README with 9.1)
    - _Requirements: none (stretch)_
  - [x] 8.3 **P1**: check timing: a full check takes under 3 seconds of compute
    - _Requirements: 7.3_

- [ ] 9. Submission — 18:00–19:30
  - [ ] 9.1 **P3** (P1 writes the threat model table and residual risks): README: the two-line pitch, a demo GIF in the first screen, how to run, the threat model table, residual risks, and prior art (Baldi et al. 2011; authorized PSI)
    - _Requirements: none (submission)_
  - [ ] 9.2 **P3**: record a 60–90 second demo video: two windows, a shared-carrier result, the transparency panel, the counselor, then the same credential proving trial eligibility
    - _Requirements: none (submission)_
  - [ ] 9.3 **All**, 19:15–19:30: confirm `.kiro/` is committed, remove the README "Team plan" section, push to GitHub, verify on GitHub, and submit on SundAI with the track set to Biotech & Healthcare
    - _Requirements: none (submission)_

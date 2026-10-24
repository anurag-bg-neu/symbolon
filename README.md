# Symbolon

**Know your genetic risks. Keep your genetic secrets.**

Symbolon gives you a lab-signed genetic credential that never leaves your device. Using privacy-preserving cryptography, you answer exactly one question at a time, such as *"Are we at risk together?"* or *"Am I eligible for this trial?"* — and nobody ever sees your genome: not your partner, not the server, not the counselor.

> We don't decide who you love. We make sure you're never surprised.

**Challenge track: Healthcare & Biotech** · Hackachusetts 2026 · Built with Kiro ([how we used Kiro](KIRO.md))

*Demo with synthetic data. Not medical advice.*

---

## Table of contents

- [The problem](#the-problem)
- [What it does](#what-it-does)
- [How it works](#how-it-works)
- [Why it's trustworthy](#why-its-trustworthy)
- [Quick start](#quick-start)
- [The demo, step by step](#the-demo-step-by-step)
- [Project layout](#project-layout)
- [Tech stack](#tech-stack)
- [Testing](#testing)
- [Known limitations](#known-limitations)
- [Where this can go](#where-this-can-go)
- [Prior art](#prior-art)
- [License](#license)

---

## The problem

Genetic testing could prevent a great deal of suffering, but people avoid it because DNA is the most personal data there is. It can't be changed if it leaks, and once uploaded it can be sold, breached, or used against you. It can also carry stigma that affects marriage prospects and family standing.

So couples skip carrier screening, patients miss treatments, and research stalls. **The problem isn't a lack of genetic tests. It's that people can't trust what happens to their data.**

## What it does

One credential, two use cases.

### 1 · Couple compatibility

Most serious inherited childhood diseases — cystic fibrosis, sickle cell disease, Tay-Sachs, spinal muscular atrophy — are **autosomal recessive**: a child is affected only if *both* parents pass on a broken copy of the same gene. Carriers are healthy and usually don't know. If both partners carry the same gene, each pregnancy has a 25% chance of an affected child.

Symbolon compares two partners' carrier genes with **private set intersection (PSI)**. Each partner learns only the agreed result — either *how many* conditions they share, or *which* ones — and nothing else. A counselor agent then explains the result in plain language, using curated facts from the gene panel, and suggests questions to bring to a genetic counselor.

### 2 · Clinical trial eligibility

The same credential proves *"I carry the variant this trial requires."* The lab signs each finding separately (for example a germline BRCA1 variant), so the patient shares exactly one signed claim — this is **selective disclosure**. The trial site verifies the lab's signature *and* a signature from the patient's wallet over a fresh challenge (so a copied claim can't be replayed), and sees nothing else.

## How it works

```
          Lab (issues signed credentials)
           │                        │
           ▼                        ▼
    Alex's wallet  ◄──►  Relay  ◄──►  Sam's wallet         Trial verifier
    (credential +      (untrusted;    (credential +        (checks one
     secret key)        sees only      secret key)          signed claim)
                        scrambled
                        points)
```

**In plain terms.** Each partner "padlocks" every item on their list with their own secret key, then they swap lists. Each adds a *second* padlock to the other's items. An item wearing both padlocks looks identical no matter whose lock went on first — so matching double-locked items reveals the overlap, and nothing is ever unlocked.

**In technical terms.** Two-party Diffie-Hellman PSI over **ristretto255** on lab-issued tokens `T_g = k_lab · H(gene)`, with **commit-then-reveal** through an untrusted relay. Matching is by **gene, not exact variant**, because two different harmful variants in the same gene can still produce an affected child. The full protocol, data models, and threat model live in [`.kiro/specs/genematch/design.md`](.kiro/specs/genematch/design.md).

## Why it's trustworthy

| Concern | How Symbolon handles it |
|---|---|
| Faking results | Only the lab can create tokens and signed claims. |
| A partner probing your whole carrier status | Lab-issued tokens plus a signed count cap stop someone from entering every gene in the panel. |
| Changing your input after seeing the partner's | Commit-then-reveal: both sides lock in before anything is revealed. |
| The server learning anything | The relay sees only scrambled points. Results are computed on each device. |
| The counselor learning your genes | In count mode it receives only a number. In detail mode it receives only what both partners already agreed to learn. |
| A copied trial claim being replayed | Each presentation requires the holder's signature over a fresh, single-use nonce. |

## Quick start

**Requirements:** Python 3.11+ (tested on 3.13). No build step for the UI.

```bash
# 1. Install the package and its dependencies (editable)
pip install -e ".[dev]"

# 2. Issue credentials for the synthetic demo people
python scripts/seed_demo.py
```

Then start all five services:

```bash
# macOS / Linux
./scripts/run_demo.sh

# Windows (PowerShell)
.\scripts\run_demo.ps1
```

| Service | URL | Purpose |
|---|---|---|
| Alex's wallet (partner A) | http://localhost:8101 | Holds a credential, runs the check |
| Sam's wallet (partner B) | http://localhost:8102 | Holds a credential, runs the check |
| Trial verifier | http://localhost:8300 | Checks one signed claim |
| Lab | http://localhost:8200 | Issues credentials (demo) |
| Relay | http://localhost:8000 | Untrusted message broker (no UI) |

**Demo sign-ins** (synthetic, shown on the sign-in page):

- Wallet: `<person>@genematch.demo` / `demo-<person>` — e.g. `alex@genematch.demo` / `demo-alex`
- Lab: `<person>@demolab.test` / `lab-<person>`, plus staff `staff@demolab.test` / `lab-staff`

Choose a different couple with `COUPLE=2 ./scripts/run_demo.sh` (1: Alex & Sam, 2: Jordan & Riley, 3: Casey & Morgan).

## The demo, step by step

**Couple check**

1. Open http://localhost:8101 and http://localhost:8102 side by side and sign in as Alex and Sam.
2. In Alex's wallet, start a check and pick a mode — *count* (how many genes shared) or *detail* (which ones).
3. Copy the invite code into Sam's wallet and accept.
4. Both wallets land on the same result. Alex and Sam share **CFTR** (cystic fibrosis). Open **Privacy details** to see exactly what each party — you, your partner, the relay, the counselor — actually saw.

**Trial eligibility**

1. Open the trial verifier at http://localhost:8300 and pick a trial (the demo includes a BRCA1 and a BRCA2 study).
2. Click **Create request**; open the generated link in Alex's wallet and approve it.
3. The verifier shows **"Eligible — verified by lab"** after receiving exactly one signed claim, and nothing else.

## Project layout

```
core/      Pure protocol + crypto, no I/O
  crypto.py      hash-to-group, blinding, commitments, canonical encoding
  protocol.py    PSI rounds and local result computation
  models.py      Pydantic wire models
  issuance.py    credential + claim issuance
  claims.py      claim hashing, presentations, verification
  labfile.py     signed lab-file parsing and verification
  passwords.py   scrypt helpers for demo accounts
lab/       Lab service: key management, issuance, Connect flow, sign-in
relay/     Untrusted relay: sessions, commit-then-reveal, message forwarding
wallet/    Per-user client service + UI (holds the credential and secret scalar)
  static/        landing, sign-in, app.js, privacy.js, counselor.js, styles
  pages/         auth-gated app shell
trial/     Trial verifier service + verifier page
agent/     Counselor agent (curated template; LLM hook for future scope)
data/      Gene panel, demo trials, synthetic records and genomes
scripts/   seed_demo, run_demo (sh / ps1), demo helpers
tests/     Unit, protocol, adversarial, property-based, UI, and backend tests
.kiro/     Specs and steering that drove the build
```

Ports: relay 8000, Alex 8101, Sam 8102, lab 8200, trial 8300. Demo keys and wallet files are written to `var/` (gitignored).

## Tech stack

- **Python 3.11+ · FastAPI · Uvicorn** — lab, relay, wallet, and trial services
- **rbcl** (libsodium bindings) — ristretto255 for the PSI
- **PyNaCl 1.6.x** — Ed25519 signatures
- **Pydantic 2** — strict wire models
- **Counselor agent** — deterministic explanations built from curated gene-panel facts, with output checks (25% risk statement, counselor referral, disclaimer) and a marked hook for a future LLM
- **Plain HTML / CSS / JS** — no frontend framework, no build step
- **Data** — autosomal-recessive gene panel plus synthetic people and genomes
- **Kiro** — spec, steering, and agent hook in [`.kiro/`](.kiro/); write-up in [`KIRO.md`](KIRO.md)

## Testing

```bash
pytest -q
```

The suite covers crypto invariants (with positive *and* negative cases), the full PSI protocol across all demo couples in both modes, adversarial transcripts (inflated sets, commitment mismatches, replay, reflection, cross-session splicing), owner-side credential tampering, trial presentation and verification, property-based checks over the crypto core, and static UI rules (banner on every page, no HTML-injection sinks, plain-language copy).

## Known limitations

Stated honestly:

- A partner could still **omit** one of their real carrier genes. Closing this needs a zero-knowledge proof that the PSI input matches the lab-signed set — designed (stretch S1) but not built.
- **Autosomal recessive only.** X-linked and dominant conditions are out of scope.
- **"No match" means lower risk** for the tested genes, never zero risk.
- Round 2 is unauthenticated, so a malicious relay or partner can force a false "no match"; the UI never presents a result as guaranteed. A DLEQ-proof fix is designed (stretch S1b).
- The same trial claim shown to two verifiers is **linkable**.
- The protocol is **not post-quantum**. Genomic data stays sensitive for life, so this matters long-term.
- **All data is synthetic.** Symbolon is an education and screening-conversation tool, not a diagnosis.

## Where this can go

The same credential could support community premarital screening (the model Dor Yeshorim has run for decades, without a central database), donor matching at fertility clinics, private drug-gene safety checks at pharmacies, and family cascade testing. **The cryptography is open; the moat is the lab network — and the fact that the genome can never leak.**

**Future scope:**
- **LLM counselor on Amazon Bedrock.** A Claude model would personalise the explanation and answer follow-up questions. The hook is already marked in `agent/counselor.py`, and `explain()` already checks any model output against the same safety rules the template meets, falling back to the template if a check fails. The privacy rule stays the same: in count mode the model would receive only a number.
- **Zero-knowledge proof of the full set** (stretch S1), so a partner can't omit a carrier gene.
- **DLEQ proofs for round 2** (stretch S1b), so neither a partner nor the relay can force a false result.
- **Post-quantum PSI** (stretch S3), because genomic data stays sensitive for life.

## Prior art

- Baldi, Baronio, De Cristofaro, Gasti, Tsudik — *Countering GATTACA: Efficient and Secure Testing of Fully-Sequenced Human Genomes*, ACM CCS 2011. Private genetic compatibility tests via private set operations.
- De Cristofaro and Tsudik — authorized private set intersection.
- Dor Yeshorim — anonymous community carrier matching, without cryptography.

## License

[MIT](LICENSE)

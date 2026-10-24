# GeneMatch

**Know your genetic risks. Keep your genetic secrets.**

GeneMatch gives you a lab-signed genetic credential that never leaves your phone. With privacy-preserving cryptography, you answer exactly one question at a time, such as *"Are we at risk together?"* or *"Am I eligible for this trial?"*, and nobody ever sees your genome: not your partner, not our servers, not our AI.

> We don't decide who you love. We make sure you're never surprised.

**Track:** Biotech & Healthcare · Hackachusetts 2026 · Built with Kiro

*Demo with synthetic data. Not medical advice.*

---

## The problem

Genetic testing could prevent a great deal of suffering, but people avoid it because DNA is the most personal data there is. It can't be changed if it leaks, and once uploaded it can be sold, breached, or used against you. It can also carry stigma that affects marriage prospects and family standing.

So couples skip carrier screening, patients miss treatments, and research stalls. **The problem isn't a lack of genetic tests. It's that people can't trust what happens to their data.**

## What we built

One credential, two use cases.

### 1. Couple compatibility

Most serious inherited childhood diseases (cystic fibrosis, sickle cell disease, Tay-Sachs, spinal muscular atrophy) are **autosomal recessive**: a child is affected only if both parents pass on a broken copy of the same gene. Carriers are healthy and usually don't know. If both partners carry the same gene, each pregnancy has a 25% chance of an affected child.

GeneMatch compares two partners' carrier genes using **private set intersection (PSI)**. Each partner learns only the agreed result, either *how many* conditions they share or *which* ones, and nothing else. An AI counselor then explains the result and options in plain language and suggests questions for a genetic counselor.

### 2. Clinical trial eligibility

The same credential is reused to prove *"I carry the variant this trial requires."* The lab signs each genetic finding separately (for example a germline BRCA1 variant), so the patient shares exactly one signed claim, which is **selective disclosure**. The trial site verifies the lab's signature, plus a signature from the patient's wallet over a fresh challenge so a copied claim can't be reused, and sees nothing else.

## How it works

```
         Lab (issues signed credentials)
          │                    │
          ▼                    ▼
   Alex's wallet ◄──► Relay ◄──► Sam's wallet        Trial verifier
   (credential +     (untrusted;   (credential +     (checks one
    secret key)      sees only      secret key)        signed claim)
                     scrambled data)
```

**Couple check, in plain terms:** each partner "padlocks" every item on their list with their own secret key and swaps lists. Each then adds a second padlock to the other's items. An item locked with both padlocks looks identical no matter whose lock went on first, so matching double-locked items reveals the overlap without anything ever being unlocked.

**Couple check, in technical terms:** two-party Diffie-Hellman PSI over ristretto255 on lab-issued tokens `T_g = k_lab · H(gene)`, with commit-then-reveal through an untrusted relay. Matching is by **gene, not exact variant**, because two different harmful variants in the same gene can still produce an affected child. The full protocol is in [`.kiro/specs/genematch/design.md`](.kiro/specs/genematch/design.md).

## Why it's trustworthy

| Concern | How GeneMatch handles it |
|---|---|
| Faking results | Only the lab can create tokens and signed claims. |
| A partner probing your whole carrier status | The gene panel is small, so plain PSI would let someone enter every gene. Lab-issued tokens plus a signed count cap prevent this. |
| Changing your input after seeing the partner's | Commit-then-reveal: both sides lock in before anything is revealed. |
| The server learning anything | The relay sees only scrambled points. Results are computed on each phone. |
| The AI learning your genes | In count mode it receives only a number. In detail mode it receives only what both partners already agreed to learn. |

**Known limitations, stated honestly:**
- A partner could still *omit* one of their real carrier genes. Closing this needs a zero-knowledge proof that the PSI input matches the lab-signed set; that's designed but not built.
- Autosomal recessive conditions only. X-linked and dominant conditions are out of scope.
- "No match" means lower risk for the tested genes, never zero risk.
- The protocol is not post-quantum. Genomic data stays sensitive for life, so this matters long-term.
- All data is synthetic, generated from public reference data.

## Tech stack

- **Python 3.11+, FastAPI**: lab, relay, and wallet services
- **rbcl** (libsodium bindings): ristretto255 for PSI. Fallback: pysodium.
- **PyNaCl 1.6.x**: Ed25519 signatures
- **Amazon Bedrock**: AI counselor, with a template fallback
- **Data**: gene panel built from ClinVar; synthetic people
- **Kiro**: specs and steering in [`.kiro/`](.kiro/); `CLAUDE.md` for AI-assisted review

## Running it

```bash
pip install -e .
python scripts/seed_demo.py     # issue credentials for the demo people
./scripts/run_demo.sh           # lab :8200, relay :8000, Alex :8101, Sam :8102, trial :8300
```

Open `localhost:8101` and `localhost:8102` side by side. Start a check as Alex, join with the invite code as Sam. For trial eligibility, open `localhost:8300`, pick a trial, and approve the request in a wallet.

## Where this can go

The same credential could support community premarital screening (the model Dor Yeshorim has run for decades, without a central database), donor matching at fertility clinics, private drug-gene safety checks at pharmacies, and family cascade testing. **The crypto is open; the moat is the lab network, and the fact that we can never leak your genome.**

## Prior art

- Baldi et al., *Countering GATTACA: Efficient and Secure Testing of Fully-Sequenced Human Genomes*, ACM CCS 2011: private genetic compatibility tests via private set operations.
- De Cristofaro and Tsudik: authorized private set intersection.
- Dor Yeshorim: anonymous community carrier matching, without cryptography.

---

## Team plan (internal, remove before final submission)

**Milestones (re-planned at 11:52):** PSI working in tests by **14:00**; two-browser demo by **16:30**; feature freeze **18:00**; submitted by **19:30** (deadline 20:00). If the two-browser demo isn't working at 16:30, cut the trial flow; the couple flow alone is a complete project.

| Time | P1: crypto and protocol | P2: backend services | P3: frontend, agent, story |
|---|---|---|---|
| 12:00–13:15 | Skeleton, rbcl check, `core/models.py`, crypto core and tests | Relay service | Gene panel (by 12:45), counselor template fallback |
| 13:15–14:00 | PSI protocol end to end in tests | Lab service, demo seed data | Counselor: Bedrock adapter and tests |
| 14:00–16:30 | Adversarial tests, then pair on wallet integration | Wallet service, `run_demo.sh` | Wallet UI, transparency panel |
| 16:30–18:00 | Claim signing and verification, trial tests | Trial service, wallet `/present` | `trials.json`, verifier page |
| 18:00–19:15 | Threat model, S1–S3 notes, timing check | Optional relay deploy, fresh-clone test | README, demo GIF, video |
| 19:15–19:30 | Push, verify on GitHub, submit (all) | | |

Full task split and handoff times: [`.kiro/specs/genematch/tasks.md`](.kiro/specs/genematch/tasks.md).

**Submission checklist:**
- [ ] `.kiro/` committed (Kiro use is required)
- [ ] Latest code pushed and visible on GitHub
- [ ] Demo GIF at the top of this README, and a 60–90 second video
- [ ] Submitted on SundAI under Biotech & Healthcare, confirmed as submitted before 20:00

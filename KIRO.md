# How we used Kiro

Symbolon was built in one day with **Kiro IDE**. Our strategy was to write the spec before the code. Steering then kept every Kiro session tied to that spec, and an agent hook re-checked the crypto rules on every save. Everything below is in [`.kiro/`](.kiro/).

| Kiro feature | Where | What it did for us |
|---|---|---|
| Spec (requirements, design, tasks) | `.kiro/specs/genematch/` | Fixed every interface up front so three owners and their Kiro sessions could build in parallel |
| Steering | `.kiro/steering/` (4 files) | Gave every session the product rules, stack, layout, and non-negotiable crypto rules |
| Agent hook | `.kiro/hooks/crypto-rules-guard.json` | Reviews every saved `core/` file against the crypto rules |
| Property-based testing | `tests/test_properties.py` | Checks six spec-linked correctness properties with Hypothesis |
| Review notes | `.kiro/notes/deferred-findings.md` | Tracked review findings that went back into the spec |

## Tools

We used **Kiro IDE** for all development: spec authoring, task execution, steering, and the agent hook. We did not use the Kiro CLI, Web, or Crew, nor any MCP servers or Kiro powers in this build. Our dependencies (libsodium bindings, PyNaCl, FastAPI) didn't need outside tooling. We spent our Kiro time on specs, steering, and the hook.

## Spec-driven development

The spec has three parts:

- **`requirements.md`**: 13 requirements, each with a user story and acceptance criteria in `WHEN … THE SYSTEM SHALL …` form (69 `SHALL` clauses). Examples are lab-certified credentials, commit-then-reveal, result-mode consent, the counselor, trial selective disclosure, plain language, and accessibility.
- **`design.md`**: the exact cryptographic construction (hash-to-group, lab tokens, the commitment formula, all three PSI rounds). It also fixes every module signature, every wire model, every error code, the check order, the threat model, and six correctness properties (P1–P6), each mapped to requirement IDs.
- **`tasks.md`**: tasks tagged with an owner (P1 crypto, P2 backend, P3 frontend and agent), a timeline with two go/no-go milestones, and a handoff table.

**How it improved the process.** Because `design.md` fixed every interface and data shape, nobody waited on anyone. P3 built the wallet UI against mocks of the documented wallet API. P2 built the relay before the crypto existed, because its payloads are opaque. Each Kiro session got one task, one owner's folders, and a list of requirement IDs to satisfy, which kept its changes small and reviewable. 48 tasks are checked off.

**Compared with vibe coding.** Vibe coding is fast for UI, but in a crypto protocol a plausible "simplification" can silently remove a security property. The clearest example is running PSI on raw `H(gene)` instead of lab tokens `k_lab · H(gene)`. That would let a partner enumerate the small gene panel and learn your whole carrier list. The spec states why the lab tokens exist, so neither people nor agents removed them. When a review found a gap, we updated `design.md` first and then the code. For example, round 2 is now released only after both sides have posted it, which stops a partner from copying or reordering points. Vibe coding would have patched the code and left the spec stale.

## Steering

There are four always-included steering files:

- **`product.md`**: who Symbolon is for, its product principles (nobody sees more than the agreed result; never a diagnosis), and its non-goals.
- **`tech.md`**: the stack, the allowed crypto libraries, and the non-negotiable crypto rules (scalar source, point validation, canonical encoding, domain-separated hashes, what may be logged).
- **`structure.md`**: the folder layout and service ports, so generated code lands in the right place.
- **`rules.md`**: working rules (stay in your owner's folders, a positive and a negative test for every crypto function) plus domain and UI rules. Examples: state the 25% risk, keep gene names out of the Count-mode counselor prompt, show the synthetic-data banner on every page.

**The strategy that made the biggest difference:** `rules.md` pulls in the whole design with `#[[file:.kiro/specs/genematch/design.md]]` and states "if code and spec disagree, the spec wins." Every Kiro session therefore had the exact prefixes, models, and check order in context. Code written in separate sessions by different owners used the same names, wire formats, and error codes, so the services could be integrated against one shared contract.

## Agent hooks

- **`crypto-rules-guard`** (`.kiro/hooks/crypto-rules-guard.json`) runs after a Python file anywhere under `core/`, including nested directories, is saved. It reviews the file against the crypto rules in `.kiro/steering/rules.md` and `.kiro/steering/tech.md` (rules.md wins if they differ), using `design.md` for exact prefixes and check order. It covers:
  - hash-to-group
  - scalar sources (`secrets` is allowed for salts, nonces, seeds, and shuffles)
  - received-point and identity validation
  - lab tokens for PSI
  - the pre-round-2 checks
  - scalar zeroing
  - no logging or sensitive data in errors
  - canonical encoding and domain prefixes
  - allowed crypto dependencies
  - positive and negative test coverage

  It reports the file, line, rule number, and a proposed fix for each violation, and it never edits code. A clean review replies `crypto-rules-guard: pass`.
- **Verified catch (2026-10-24, Kiro IDE 1.4.26):** the agent created an unimported probe file, `core/_kiro_hook_probe.py`.
  - It first saved a compliant version, `return crypto.random_scalar()`, and the hook replied `crypto-rules-guard: pass`.
  - It then changed line 8 to the hardcoded `return bytearray(32)`. The `PostFileSave` guard reported `core/_kiro_hook_probe.py:8` as a rule 2 violation and proposed restoring `return crypto.random_scalar()`.
  - The probe file was then deleted.

**Why it helped.** The hook reads the steering files instead of keeping its own copy of the rules, so the policy lives in one place and can't drift. Each crypto change got a focused security review within seconds of saving, without anyone having to remember to ask.

## Property-based testing

`tests/test_properties.py` uses Hypothesis to exercise the crypto and protocol invariants defined in `design.md`:

| ID | Property | Requirements |
|---|---|---|
| **P1 Correctness** | PSI returns exactly the generated set intersection in Count and Detail modes, with equal outputs for both parties. | 3.4, 5.5, 5.6 |
| **P2 Commutativity** | Double blinding commutes for generated non-zero canonical scalars and panel genes. | 5.5, 5.6 |
| **P3 Order independence** | Permuting either party's issuance input does not change the result. | 5.5, 5.6 |
| **P4 Validation** | Generated invalid 32-byte ristretto255 encodings and the identity are rejected. | 5.4 |
| **P5 Key separation** | The same gene produces different tokens under distinct lab keys. | 1.1, 1.4 |
| **P6 Unlinkability** | Distinct fresh session scalars produce different round-1 blinded point values for the same non-empty input. | 5.1, 5.2 |

P6 covers the wire's blinded point values, not full transcript unlinkability. Signed credential metadata stays stable and linkable, as documented in the threat model.

**What PBT added over unit tests.** Our unit tests cover the three fixed demo couples. The properties cover any pair of subsets of the panel, any scalars, and any byte strings. P1 and P3 are the guarantees users actually rely on: the right answer for every possible couple, regardless of input order. P4 tries random 32-byte strings that unit tests would never think of. The first property run found no counterexamples or protocol bugs, so no protocol behaviour changed. The properties now guard the protocol against regressions alongside the 446 unit, protocol, adversarial, and UI tests.

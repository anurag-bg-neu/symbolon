# Kiro project guidance

## Agent hooks

- **`crypto-rules-guard`** (`.kiro/hooks/crypto-rules-guard.json`) runs after a Python file directly under `core/` is saved. It reviews the file against the crypto rules in `.kiro/steering/rules.md` and `.kiro/steering/tech.md` (rules.md wins if they differ), using `design.md` for exact prefixes and check order. It covers hash-to-group, scalar sources (`secrets` is allowed for salts, nonces, seeds, and shuffles), received-point and identity validation, lab tokens for PSI, the pre-round-2 checks, scalar zeroing, no logging or sensitive data in errors, canonical encoding and domain prefixes, allowed crypto dependencies, and positive/negative test coverage. It reports the file, line, rule number, and a proposed fix for each violation; it never edits code. A clean review replies `crypto-rules-guard: pass`.

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

P6 covers the wire's blinded point values, not full transcript unlinkability: signed credential metadata remains stable and linkable as documented in the threat model. The initial property run found no counterexamples or protocol bugs, so no protocol behavior was changed.

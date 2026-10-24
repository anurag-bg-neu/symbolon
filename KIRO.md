# Kiro project guidance

## Agent hooks

- **`crypto-rules-guard`** (`.kiro/hooks/crypto-rules-guard.json`) runs after a Python file directly under `core/` is saved. It reviews the file against the crypto rules in `.kiro/steering/rules.md` and `.kiro/steering/tech.md` (rules.md wins if they differ), using `design.md` for exact prefixes and check order. It covers hash-to-group, scalar sources (`secrets` is allowed for salts, nonces, seeds, and shuffles), received-point and identity validation, lab tokens for PSI, the pre-round-2 checks, scalar zeroing, no logging or sensitive data in errors, canonical encoding and domain prefixes, allowed crypto dependencies, and positive/negative test coverage. It reports the file, line, rule number, and a proposed fix for each violation; it never edits code. A clean review replies `crypto-rules-guard: pass`.

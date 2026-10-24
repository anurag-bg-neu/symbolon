# Kiro project guidance

## Agent hooks

- **`crypto-rules-guard`** (`.kiro/hooks/crypto-rules-guard.json`) runs after a Python file under `core/` is saved. It reviews the saved file against the authoritative cryptographic rules in `.kiro/steering/tech.md`, including gene-point derivation, scalar generation, received-point and identity validation, use of lab tokens for PSI, secret-free logging and errors, and positive/negative crypto test coverage. It reports the file, line, broken rule, and a proposed fix for each violation; it never edits code silently. A clean review replies `crypto-rules-guard: pass`.

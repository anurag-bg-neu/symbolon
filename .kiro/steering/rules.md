# Rules (never break these)

Interfaces, file paths, and models are defined in `#[[file:.kiro/specs/genematch/design.md]]`. If code and spec disagree, the spec wins. If the spec is wrong, update `design.md` first, then the code, and say so.

## Working
- Take only tasks in `tasks.md` tagged with your owner (P1, P2, P3) and stay inside that owner's folders. Shared files (`core/models.py`, `pyproject.toml`) change only after telling the team.
- Implement the smallest change that meets the listed requirement IDs. Every crypto function gets a positive and a negative test. Tick a task only when `pytest -q` passes.
- Report what changed, which requirement IDs it satisfies, and what was deferred.

## Crypto
- Group: ristretto255. `H(g) = from_hash(SHA-512(b"genematch/v1/gene/" + gene_id))`.
- Scalars come only from `crypto_core_ristretto255_scalar_random()`. Salts and nonces come from `secrets`.
- Validate every received point; reject invalid points and the identity.
- PSI runs on lab tokens `T_g = k_lab · H(g)`, never on raw `H(g)`. This stops a partner from probing the whole panel. Do not simplify it away.
- Enforce commit-then-reveal, the lab signature check, and `|blinded set| == signed count <= MAX_SET` before any round-2 computation.
- Session scalars live only in wallet memory: never sent, persisted, or logged. Zero them on completion or abort.
- Signed or hashed data uses canonical JSON (sorted keys, no whitespace, UTF-8, lowercase hex for bytes) and a `genematch/v1/...` domain prefix. Use the exact prefixes in `design.md`.
- Log only 8-character hex prefixes of blinded points. Never log scalars, tokens, gene IDs, claims, or credentials.
- Never use `random`, `hash()`, or homemade crypto. Do not add crypto dependencies beyond those in `tech.md` without asking the team.

## Domain
- Match carrier genes by gene, not by exact variant.
- Autosomal recessive genes only in the panel. X-linked and dominant conditions are out of scope.
- Trial claims use germline findings (BRCA1/BRCA2), never tumor mutations.
- The counselor states the 25% per-pregnancy risk when a gene is shared, recommends a genetic counselor, never diagnoses, and never tells anyone whether to marry or have children.
- In Count mode, the counselor's prompt contains no gene or condition names. A test asserts this; keep it passing.
- Panel links use MedlinePlus Genetics condition pages (`https://medlineplus.gov/genetics/condition/<slug>/`). Do not invent URLs; leave a gene out if you cannot confirm its page.

## UI and copy
- Every page shows: "Demo with synthetic data. Not medical advice."
- User-facing errors are plain language and say what was not revealed, for example: "Your partner's results couldn't be verified, so the check was stopped. Nothing about you was shared."
- Plain HTML/CSS/JS. No framework, no build step.

## Never claim otherwise
- A partner can omit a real carrier gene; preventing this needs a ZK proof (stretch S1).
- The same trial claim shown to two verifiers is linkable.
- Not post-quantum.
- Synthetic data only.

## Out of scope unless the team asks
Real patient data, user accounts, databases (in-memory stores and the `var/` demo files are fine), mobile apps, a frontend framework, and the ZK stretch goals.

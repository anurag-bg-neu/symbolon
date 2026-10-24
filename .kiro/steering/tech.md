# Tech stack and conventions

## Stack
- Language: Python 3.11+
- Web: FastAPI + Uvicorn for all three services (lab, relay, wallet); plain HTML/JS for the wallet UI (no build step).
- Crypto: ristretto255 via `rbcl` (libsodium bindings: `crypto_core_ristretto255_from_hash`, `crypto_scalarmult_ristretto255`, `crypto_core_ristretto255_scalar_random`, `crypto_core_ristretto255_is_valid_point`); fallback `pysodium`. Ed25519 signatures via PyNaCl 1.6.x (`nacl.signing`). SHA-256/512 via `hashlib`.
- LLM: Amazon Bedrock (Claude) via `boto3` behind a small adapter; template fallback when no credentials.
- Fallback PSI library (last resort only): `openmined-psi` 2.0.x, which is one-sided.
- Pin exact versions in `pyproject.toml` after the first successful install.
- Tests: pytest.

## Crypto rules (non-negotiable)
- Group: ristretto255. Hash-to-group: `crypto_core_ristretto255_from_hash(SHA-512(b"genematch/v1/gene/" + gene_id))`.
- Scalars from `crypto_core_ristretto255_scalar_random()` only. Never derive scalars from user input.
- Validate every received point with `crypto_core_ristretto255_is_valid_point`; reject identity.
- Never log scalars, tokens, gene IDs, or credentials. Log only hex prefixes (first 8 chars) of blinded points.
- Canonical encoding for anything signed or hashed: JSON with sorted keys, no whitespace, UTF-8; byte arrays as lowercase hex.
- Domain-separate every hash with a `genematch/v1/...` prefix.

## Code conventions
- Type hints everywhere; dataclasses or Pydantic models for all wire messages.
- Pure functions in `core/`; services only orchestrate.
- Every crypto function has a unit test, including a negative test.

# Demo wallet examples

These JSON files show the exact shape and synthetic findings of Alex and Sam's wallet files. Sensitive cryptographic values are deliberately replaced with `<redacted ...>` placeholders, so the examples are documentation and cannot be loaded by the wallet service.

Generate valid local wallets from the repository root with:

```bash
python scripts/seed_demo.py
```

The command writes `var/wallets/alex.json`, `var/wallets/sam.json`, and the other demo wallets. `var/` is gitignored, and each wallet file is created with owner-only `0600` permissions.

A generated wallet contains:

- the owner's sorted carrier-gene names;
- one lab-authorized Ristretto token per gene, in the same order;
- the signed credential and private commitment salt;
- separately signed trial claims;
- the holder's private signing seed; and
- the lab's public verification key.

The genome manifests under `data/synthetic_genomes/` are not copied into wallet files. Only the simulated, reviewed carrier and trial labels are issued. Wallet secrets, full tokens, claims, and credentials must never be committed or logged.

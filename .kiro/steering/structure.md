# Repository structure

```
genematch/
  core/            # pure protocol + crypto (no I/O)
    crypto.py      # hash-to-group, blinding, commitments, encoding
    protocol.py    # PSI rounds, result computation
    models.py      # Pydantic wire models
    issuance.py    # credential + claim issuance (called by lab and seed script)
    claims.py      # claim hashing, presentations, verify_presentation
  lab/             # lab service: keys, credential issuance
    service.py
    keys.py        # load_or_create() for var/lab_keys.json
  relay/           # relay service: sessions, commit-then-reveal, message forwarding
    service.py
  wallet/          # per-user client service + UI (holds credential and secret scalar)
    service.py
    static/        # index.html, app.js, styles.css
  trial/           # trial verifier service + page (selective disclosure)
    service.py
    static/
  agent/           # counselor agent
    counselor.py
    prompts.py
  data/
    panel.json     # curated autosomal-recessive gene panel with condition facts
    trials.json    # demo trials and their required germline findings
  scripts/
    seed_demo.py   # issues credentials for demo couples
    run_demo.sh    # starts lab:8200, relay:8000, wallets on 8101/8102, trial:8300
  tests/
  var/             # gitignored: lab_keys.json, wallets/<person_id>.json
  README.md
```

Ports: relay 8000, Alex wallet 8101, Sam wallet 8102, lab 8200, trial verifier 8300.

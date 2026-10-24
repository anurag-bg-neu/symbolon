# Deferred review findings (P1 notes)

Working notes, not a spec. Status as of P2 commit `07dcbcf`.

## Resolved by P2 in 07dcbcf
- Round 2 released only after both sides post it (no copy/reorder by a partner, honest relay).
- `/check/seen` relay entries now include round-1 signed metadata.
- Mode text discloses that both partners learn each other's carrier count.
- `/check/start` maps non-200 relay responses to `RELAY_UNAVAILABLE`.
- Relay payload caps: 16 KB body, 8 fields, 256-char strings, `MAX_SET` list items, 10,000 sessions.

## Still open (deliberately deferred)
- Round 2 unauthenticated: a malicious relay can still fake a match or force "no match"; a partner can force "no match". Needs DLEQ (design.md S1b).
- Omission of real carrier genes is undetectable (S1).
- Fairness: one side can abort after learning the result.
- `/check/seen` relay values come from the untrusted relay log; the UI must render them with `textContent`, never `innerHTML`.
- Relay `/fail` accepts any caller who knows the session ID (no per-role auth); acceptable for the demo since session IDs are 128-bit random.
- Lab `POST /credentials` is unauthenticated; kept closed unless `GENEMATCH_LAB_OPEN=1`.

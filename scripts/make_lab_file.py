"""Lab-side tooling: make a lab-signed carrier file for a synthetic person (testing only).

This signs with the demo lab's private keys in var/lab_keys.json, so running it is
the same as being the lab. Patients never get this ability: in the product they can
only download the lab's own record for themselves ("Download your results file").
Same checks as issuance (panel genes, autosomal recessive, no duplicates, at most 20).

  macOS/Linux: .venv/bin/python scripts/make_lab_file.py --person alex --genes CFTR,HBB --out alex.json
  Windows:     .venv\\Scripts\\python.exe scripts\\make_lab_file.py --person alex --genes CFTR,HBB --out alex.json
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from core.issuance import IssuanceError  # noqa: E402
from lab.service import load_records, make_custom_lab_file  # noqa: E402


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Create a synthetic, lab-signed Symbolon lab file.")
    ap.add_argument("--person", required=True, help="wallet owner, for example alex")
    ap.add_argument("--genes", required=True, help="comma-separated genes, for example CFTR,HBB")
    ap.add_argument("--out", required=True, type=Path, help="output JSON path")
    ap.add_argument("--name", help="display name (default: the lab's name for the person)")
    args = ap.parse_args(argv)

    records = load_records()
    name = args.name or records.get(args.person, {}).get("display_name", args.person)
    genes = [g.strip() for g in args.genes.split(",") if g.strip()]
    try:
        doc = make_custom_lab_file(args.person, genes, name)
    except IssuanceError as e:
        print(f"The lab can't issue this file: {e}.", file=sys.stderr)
        return 2
    args.out.write_text(json.dumps(doc, indent=2), "utf-8")
    print(f"wrote {args.out} for {name}: {', '.join(doc['genes'])} (synthetic)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

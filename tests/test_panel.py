"""Task 2.1: the curated panel and trials files."""

import json
import re
from pathlib import Path

from core.models import CLAIM_FINDING, CLAIM_GENES, PanelEntry

ROOT = Path(__file__).resolve().parents[1]
DEMO_GENES = {"ASPA", "CFTR", "FANCC", "GBA", "HBB", "HEXA", "PAH", "SMN1"}
URL = re.compile(r"^https://medlineplus\.gov/genetics/condition/[a-z0-9-]+/$")


def test_panel_entries_are_valid_ar_with_condition_links():
    panel = json.loads((ROOT / "data" / "panel.json").read_text("utf-8"))
    assert 8 <= len(panel) <= 60
    genes = [PanelEntry.model_validate(e).gene for e in panel]
    assert len(genes) == len(set(genes))
    assert all(e["inheritance"] == "AR" for e in panel)
    assert all(URL.match(e["medlineplus"]) for e in panel)
    assert DEMO_GENES <= set(genes)
    assert not CLAIM_GENES & set(genes)  # BRCA findings are claims, not carrier genes


def test_trials_are_germline_brca():
    trials = json.loads((ROOT / "data" / "trials.json").read_text("utf-8"))
    assert {t["required"]["gene"] for t in trials} == {"BRCA1", "BRCA2"}
    for t in trials:
        assert set(t) == {"trial_id", "name", "summary", "required"}
        assert t["required"]["finding"] == CLAIM_FINDING

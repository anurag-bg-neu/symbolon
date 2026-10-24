"""Task 6.3 (template only): Requirement 6 statements and the Count-mode prompt."""

import json
from pathlib import Path

import pytest

from agent import counselor
from core.models import Explanation, Result

ROOT = Path(__file__).resolve().parents[1]
PANEL = json.loads((ROOT / "data" / "panel.json").read_text("utf-8"))
NAMES = [e["gene"] for e in PANEL] + [e["condition"] for e in PANEL]


def text(e: Explanation) -> str:
    return " ".join([e.summary, e.what_it_means, *e.next_steps, *e.questions, e.disclaimer])


@pytest.mark.parametrize("n", [0, 1, 2])
def test_count_mode_prompt_and_output_have_no_names(n):
    r = Result(mode="count", shared_count=n, shared_genes=None)
    prompt = counselor.build_prompt(r, PANEL)
    out = text(counselor.explain(r, PANEL))
    for name in NAMES:
        assert name not in prompt, name
        assert name not in out, name
    assert counselor.counselor_input(r, PANEL) == {"shared_count": n}


@pytest.mark.parametrize("r", [
    Result(mode="detail", shared_count=1, shared_genes=["CFTR"]),
    Result(mode="detail", shared_count=2, shared_genes=["HBB", "SMN1"]),
    Result(mode="count", shared_count=2, shared_genes=None),
])
def test_shared_meets_requirement_6(r):
    e = counselor.explain(r, PANEL)
    t = text(e).lower()
    assert e.source == "template" and counselor.valid(e, r)
    assert "25%" in t and "genetic counselor" in t and "options exist" in t
    assert "does not recommend" in t and "not a diagnosis" in t
    assert 3 <= len(e.questions) <= 5 and e.disclaimer
    for word in ("marry", "should not have children", "you have "):
        assert word not in t


def test_detail_uses_only_panel_facts():
    r = Result(mode="detail", shared_count=1, shared_genes=["CFTR"])
    e = counselor.explain(r, PANEL)
    cftr = next(p for p in PANEL if p["gene"] == "CFTR")
    t = text(e)
    assert cftr["condition"] in t and cftr["medlineplus"] in t
    others = [p["condition"] for p in PANEL if p["gene"] != "CFTR"]
    assert not any(c in t for c in others)


@pytest.mark.parametrize("mode", ["count", "detail"])
def test_none_shared_meets_requirement_6(mode):
    r = Result(mode=mode, shared_count=0, shared_genes=None if mode == "count" else [])
    e = counselor.explain(r, PANEL)
    t = text(e).lower()
    assert counselor.valid(e, r)
    assert "greatly reduced" in t and "not zero" in t and "not tested" in t
    assert "genetic counselor" in t and "25%" not in t
    assert 3 <= len(e.questions) <= 5


def test_invalid_llm_output_falls_back(monkeypatch):
    bad = Explanation(summary="All fine.", what_it_means="Nothing to worry about.",
                      next_steps=[], questions=["?"], disclaimer="", source="llm")
    monkeypatch.setattr(counselor, "_llm_explain", lambda r, p: bad)
    r = Result(mode="detail", shared_count=1, shared_genes=["CFTR"])
    assert counselor.explain(r, PANEL).source == "template"


def test_llm_exception_falls_back(monkeypatch):
    def boom(r, p):
        raise TimeoutError
    monkeypatch.setattr(counselor, "_llm_explain", boom)
    r = Result(mode="count", shared_count=0, shared_genes=None)
    assert counselor.explain(r, PANEL).source == "template"


def test_no_llm_code_by_default():
    src = (ROOT / "agent" / "counselor.py").read_text("utf-8")
    assert "boto3" not in src and "bedrock" not in src.lower()
    assert counselor._llm_explain(Result(mode="count", shared_count=0,
                                          shared_genes=None), PANEL) is None

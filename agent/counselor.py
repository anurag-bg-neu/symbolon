"""Counselor agent: turns the agreed result into a plain-language explanation.

Template only (no LLM is called). Uses only curated panel facts. In Count mode it
receives only the number, so no gene or condition name can appear.

Optional feature: deleting the agent/ package disables it. The wallet then returns
explanation=null and the UI still shows the required safety text.
"""

from __future__ import annotations

import json
from collections.abc import Iterable, Mapping
from typing import Any

from agent.prompts import (
    DISCLAIMER,
    NONE_SHARED_QUESTIONS,
    SHARED_QUESTIONS,
    SYSTEM_PROMPT,
)
from core.models import Explanation, Result

RISK = "each pregnancy has a 25% (1 in 4) chance of a child being affected"
REFERRAL = "We recommend talking with a genetic counselor"


def _index(panel: Iterable[Any] | Mapping[str, Any]) -> dict[str, dict[str, Any]]:
    items = panel.values() if isinstance(panel, Mapping) else panel
    out = {}
    for e in items:
        d = e if isinstance(e, Mapping) else e.model_dump()
        out[d["gene"]] = dict(d)
    return out


def counselor_input(result: Result, panel) -> dict[str, Any]:
    """Exactly what the counselor is given. Count mode: the number only."""
    if result.mode == "count":
        return {"shared_count": result.shared_count}
    idx = _index(panel)
    return {"shared": [idx[g] for g in (result.shared_genes or []) if g in idx]}


def build_prompt(result: Result, panel) -> str:
    """The prompt an LLM would receive. Kept separate so tests can inspect it."""
    return SYSTEM_PROMPT + "\n\nFacts:\n" + json.dumps(counselor_input(result, panel),
                                                       sort_keys=True)


def _template(result: Result, panel) -> Explanation:
    facts = counselor_input(result, panel)
    n = result.shared_count
    if n == 0:
        return Explanation(
            summary="You don't share a carrier gene for any condition on the tested panel.",
            what_it_means=(
                "This means the chance of a child being affected by the tested conditions "
                "is greatly reduced, but it is not zero. Conditions that were not tested "
                "are not covered by this result."),
            next_steps=[
                f"{REFERRAL} if you have questions about conditions that were not tested "
                "or about your family history.",
            ],
            questions=list(NONE_SHARED_QUESTIONS),
            disclaimer=DISCLAIMER,
            source="template",
        )

    if "shared" in facts:
        names = [f"{e['condition']} ({e['gene']})" for e in facts["shared"]]
        what = "You both carry a variant in the same gene for: " + "; ".join(names) + "."
        details = [f"{e['condition']}: {e['summary']} More information: {e['medlineplus']}"
                   for e in facts["shared"]]
    else:
        what = (f"You both carry a variant in the same gene for {n} condition"
                f"{'s' if n != 1 else ''} on the tested panel. You agreed to learn only "
                "the number, so the conditions are not named here.")
        details = []

    return Explanation(
        summary=(f"You share {n} carrier gene{'s' if n != 1 else ''}. Being a carrier is "
                 "common, and carriers are usually healthy."),
        what_it_means=(
            f"{what} When two partners carry the same gene, {RISK}. "
            "This is a chance, not a certainty, and it is not a diagnosis."),
        next_steps=[
            f"{REFERRAL}. They can explain this result and what it means for you.",
            "Options exist for couples in this situation. A genetic counselor can walk you "
            "through them; this tool does not recommend any.",
            *details,
        ],
        questions=list(SHARED_QUESTIONS),
        disclaimer=DISCLAIMER,
        source="template",
    )


def valid(e: Explanation, result: Result) -> bool:
    """Requirement 6.3–6.6 checks, for the template and any future LLM output."""
    text = " ".join([e.summary, e.what_it_means, *e.next_steps]).lower()
    if "genetic counselor" not in text or not 3 <= len(e.questions) <= 5:
        return False
    if not e.disclaimer:
        return False
    if result.shared_count > 0:
        return "25%" in text and "options" in text
    return "not zero" in text and "not tested" in text


# --- LLM HOOK -------------------------------------------------------------------
# To add an LLM later: implement _llm_explain(result, panel) to send
# build_prompt(result, panel) to the model and return an Explanation with
# source="llm", or None on any failure or timeout. explain() already validates the
# output and falls back to the template. No LLM code exists yet, by design.
def _llm_explain(result: Result, panel) -> Explanation | None:
    return None
# --------------------------------------------------------------------------------


def explain(result: Result, panel) -> Explanation:
    try:
        out = _llm_explain(result, panel)
    except Exception:
        out = None
    if out is not None and valid(out, result):
        return out
    return _template(result, panel)

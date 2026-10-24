"""Counselor wording. Plain language; never diagnoses or advises on relationships."""

DISCLAIMER = (
    "This is general education based on a demo with synthetic data. It is not a "
    "diagnosis or medical advice. A genetic counselor can explain what this means for you."
)

# System prompt for a future LLM adapter (see counselor.py, "LLM HOOK"). Unused today.
SYSTEM_PROMPT = (
    "You explain a carrier-screening result to a couple in plain, calm language. "
    "Use only the facts provided. Do not diagnose. Do not tell anyone whether to marry "
    "or have children. Always recommend talking to a genetic counselor. If genes are "
    "shared, state that each pregnancy has a 25% chance of an affected child and that "
    "options exist, without recommending any. If none are shared, say the risk for the "
    "tested conditions is greatly reduced but not zero, and untested conditions are not "
    "covered. Give 3 to 5 questions to ask a genetic counselor."
)

SHARED_QUESTIONS = [
    "What does carrying the same gene mean for each of our pregnancies?",
    "Which options are available to us, and what does each involve?",
    "Should we confirm these results with clinical testing?",
    "Should other family members consider carrier screening?",
]

NONE_SHARED_QUESTIONS = [
    "Which conditions were not included in this screening?",
    "Would a broader carrier panel be useful for us?",
    "How does our family history or ancestry affect any remaining risk?",
]

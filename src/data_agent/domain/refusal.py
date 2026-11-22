"""The refusal label (D-013, D-029), set by code from facts, so the same facts always get the same
label. The question's region words (else the model's report) say which regions were asked; the
database says which regions the user can see."""

import re

from margin_copilot_shared.contracts import Refusal

# "Centre" and "Central" stay out: Tétouan Centre is a North store.
REGION_WORDS = re.compile(r"\b(north(?:ern)?|south(?:ern)?|center)\b", re.IGNORECASE)


def regions_named(question: str) -> list[str]:
    """The model's report of the regions asked varies between runs; the question doesn't."""
    named = {match.group(1)[0].upper() for match in REGION_WORDS.finditer(question)}
    return [region for region in ("N", "C", "S") if region in named]


def refusal_for(regions_asked: list[str], found_data: bool, visible: list[str]) -> Refusal | None:
    """out_of_scope isn't decided here: scope rules belong to the orchestrator (milestone 7)."""
    asked = set(regions_asked) or set(visible)
    if not asked & set(visible):
        return "no_access"
    return None if found_data else "no_data"

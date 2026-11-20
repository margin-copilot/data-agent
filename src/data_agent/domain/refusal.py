"""The refusal label (D-013, D-029), set by code from facts, so the same facts always get the same
label. The model reports the facts; the database says which regions the user can see."""

from margin_copilot_shared.contracts import Refusal


def refusal_for(
    about_pricing: bool, regions_asked: list[str], found_data: bool, visible: list[str]
) -> Refusal | None:
    if not about_pricing:
        return "out_of_scope"
    asked = set(regions_asked) or set(visible)
    if not asked & set(visible):
        return "no_access"
    return None if found_data else "no_data"

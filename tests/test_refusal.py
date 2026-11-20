import pytest

from data_agent.domain.refusal import refusal_for


@pytest.mark.parametrize(
    ("about_pricing", "asked", "found", "visible", "expected"),
    [
        (True, ["N"], True, ["N"], None),
        (True, [], True, ["N"], None),  # no region named: the user's own
        (True, ["N", "C", "S"], True, ["N"], None),  # partly answerable: answered, no label
        (True, ["S"], False, ["S"], "no_data"),  # a closed store
        (True, [], False, ["N"], "no_data"),
        (True, ["S"], False, ["N"], "no_access"),
        (True, ["S"], True, ["N"], "no_access"),  # the model can't have data for a hidden region
        (True, ["C", "S"], False, ["N"], "no_access"),
        (True, ["S"], False, ["C", "N", "S"], "no_data"),  # the director
        (False, [], False, ["N"], "out_of_scope"),
        (False, ["S"], False, ["N"], "out_of_scope"),
    ],
)
def test_the_label_follows_from_the_facts(about_pricing, asked, found, visible, expected):
    assert refusal_for(about_pricing, asked, found, visible) == expected

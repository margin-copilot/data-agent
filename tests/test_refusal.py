import pytest

from data_agent.domain.refusal import refusal_for


@pytest.mark.parametrize(
    ("asked", "found", "visible", "expected"),
    [
        (["N"], True, ["N"], None),
        ([], True, ["N"], None),  # no region named: the user's own
        (["N", "C", "S"], True, ["N"], None),  # partly answerable: answered, no label
        (["S"], False, ["S"], "no_data"),  # a closed store
        ([], False, ["N"], "no_data"),
        (["S"], False, ["N"], "no_access"),
        (["S"], True, ["N"], "no_access"),  # the model can't have data for a hidden region
        (["C", "S"], False, ["N"], "no_access"),
        (["S"], False, ["C", "N", "S"], "no_data"),  # the director
    ],
)
def test_the_label_follows_from_the_facts(asked, found, visible, expected):
    assert refusal_for(asked, found, visible) == expected

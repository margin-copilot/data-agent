import pytest

from data_agent.domain.refusal import refusal_for, regions_named


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


@pytest.mark.parametrize(
    ("question", "named"),
    [
        ("Why did dairy margin drop in the North in October 2026?", ["N"]),
        ("Compare southern and Northern dairy margins.", ["N", "S"]),
        ("What price changes did the Center make to olive oil in June 2026?", ["C"]),
        ("What did south.mgr change in August?", ["S"]),
        ("Sales at Tétouan Centre in October?", []),  # a North store, not the Center
        ("Dairy margin in October?", []),
    ],
)
def test_code_reads_the_regions_a_question_names(question, named):
    assert regions_named(question) == named

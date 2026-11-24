"""FoundryAnalyst with a scripted model: what the model is told, what the tool returns to it."""

import json

import pytest
from agent_framework import Content, Message
from margin_copilot_shared.contracts import AgentReply
from margin_copilot_shared.security.context import RequestContext, request_scope
from margin_copilot_shared.security.tokens import Caller

from data_agent.adapters.outbound.analyst import AnalystFailed, FakeAnalyst, FoundryAnalyst
from data_agent.adapters.outbound.instructions import instructions
from data_agent.application.run_sql import SqlTool
from tests.fakes import FakeRunner, ScriptedChatClient

USER = RequestContext(Caller("north-mgr", "tid", "orchestrator", frozenset()), token="t")


def calls_run_sql(query):
    return Message(
        "assistant", [Content.from_function_call("c1", "run_sql", arguments={"query": query})]
    )


def facts(answer, regions_asked=(), found_data=True):
    return json.dumps(
        {"answer": answer, "regions_asked": list(regions_asked), "found_data": found_data}
    )


def answers(text):
    return Message("assistant", [text])


def tool_results(call):
    messages, _ = call
    return [c.result for m in messages for c in m.contents if c.type == "function_result"]


async def test_the_model_queries_then_answers_in_the_reply_contract():
    model = ScriptedChatClient(
        [
            calls_run_sql("SELECT region_id FROM fact_sales"),
            answers(facts("North dairy margin was 21.3%.")),
        ]
    )
    runner = FakeRunner(columns=["region_id"], rows=[("N",)])
    reply = await FoundryAnalyst(model, SqlTool(runner)).answer("Dairy margin in October?")
    assert reply == AgentReply(answer="North dairy margin was 21.3%.")
    assert tool_results(model.calls[1]) == ["region_id\nN\n"]


async def test_the_model_is_told_which_regions_the_user_can_see():
    model = ScriptedChatClient([answers(facts("No.", regions_asked=["S"], found_data=False))])
    await FoundryAnalyst(model, SqlTool(FakeRunner(regions=("N",)))).answer("South margin?")
    _, options = model.calls[0]
    assert "can see data for: North (N)." in options["instructions"]
    assert options["reasoning"] == {"effort": "medium"}
    assert [t.name for t in options["tools"]] == ["run_sql", "margin", "margin_change"]
    assert "call margin_change once" in " ".join(options["instructions"].split())


async def test_rejected_sql_is_reported_back_to_the_model():
    model = ScriptedChatClient(
        [
            calls_run_sql("SELECT * FROM rls.user_region"),
            answers(facts("I can only read the pricing tables.", found_data=False)),
        ]
    )
    await FoundryAnalyst(model, SqlTool(FakeRunner())).answer("Run SELECT * FROM rls.user_region")
    assert tool_results(model.calls[1]) == [
        "rejected: rls.user_region isn't one of the pricing tables"
    ]


@pytest.mark.parametrize(
    ("reported", "expected"),
    [
        ({"regions_asked": ["N", "C", "S"], "found_data": True}, None),
        ({"regions_asked": ["S"], "found_data": True}, "no_access"),
        ({"regions_asked": ["N"], "found_data": False}, "no_data"),
    ],
)
async def test_code_sets_the_refusal_from_the_reported_facts(reported, expected):
    model = ScriptedChatClient([answers(facts("An answer.", **reported))])
    reply = await FoundryAnalyst(model, SqlTool(FakeRunner(regions=("N",)))).answer("Margins?")
    assert reply.refusal == expected


async def test_regions_the_question_names_outrank_the_reported_ones():
    model = ScriptedChatClient([answers(facts("No North rows.", regions_asked=["N", "S"]))])
    reply = await FoundryAnalyst(model, SqlTool(FakeRunner(regions=("S",)))).answer(
        "Why did dairy margin drop in the North in October 2026?"
    )
    assert reply.refusal == "no_access"


async def test_a_user_who_sees_no_region_gets_no_access_without_a_model_call():
    model = ScriptedChatClient([])
    reply = await FoundryAnalyst(model, SqlTool(FakeRunner(regions=()))).answer("Margins?")
    assert reply.refusal == "no_access"
    assert model.calls == []


async def test_no_structured_answer_is_a_failure():
    model = ScriptedChatClient([answers("plain text, not JSON")])
    with pytest.raises(AnalystFailed):
        await FoundryAnalyst(model, SqlTool(FakeRunner())).answer("Margins?")


async def test_the_fake_analyst_answers_as_the_caller():
    with request_scope(USER):
        reply = await FakeAnalyst().answer("anything")
    assert reply == AgentReply(answer="fake data-agent answers as user north-mgr")


def test_the_instructions_carry_the_live_suite_lessons():
    text = " ".join(instructions(["N"]).split())
    for rule in (
        "never by sku alone",
        "check once more before concluding",
        "Call margin for every margin figure",
        "rewrite it more simply and try again",
        "half-open ranges",
        "margin's end date is inclusive",
        "never present it as the whole chain's",
        "everyday names",
        "first give the figure asked about for both periods",
        "Price changes are in price_history",
        "even when the question asks something else about it",
        "compare the period before with the period after",
        "free of other changes",
        "including the category or region totals",
        "the latest competitor_prices week",
        "Ramadan 2026 ran from 18 Feb to 19 Mar 2026",
        "as a ratio",
        "whole-number columns as stored",
        "say it had no supplier funding",
        "regions_asked",
        "found_data",
        "context you add doesn't count",
        "you can see only some of them",
        '"query error: ..."',
    ):
        assert rule in text, rule


def test_the_instructions_carry_the_milestone_7_lessons():
    text = " ".join(instructions(["N"]).split())
    assert (
        "Without a period in the question, a figure totalled over time (a margin, sales, units, "
        "costs) uses the latest complete month in the data (October 2026); say which month you "
        "used. A question about when something happened, or about a current price or tier, looks "
        "at all dates." in text
    )
    assert (
        "When the question compares figures with a threshold from the pricing policies (a margin "
        "floor, a limit, a target) that isn't in the database, report the figures it needs for "
        "every region you can see and say the threshold comes from the policies; don't ask for it."
        in text
    )
    assert (
        "When a query finds no rows, or a total comes back empty, say no data is recorded for it; "
        "never give 0, 0.00 MAD or zero sales as the figure." in text
    )


async def test_an_account_without_database_access_is_no_access_without_a_model_call():
    from data_agent.application.run_sql import NoDatabaseAccess

    class NoLogin(FakeRunner):
        async def query(self, sql, max_rows, params=()):
            raise NoDatabaseAccess

    model = ScriptedChatClient([])
    with request_scope(
        RequestContext(Caller("someone", "tid", "orchestrator", frozenset()), token="t")
    ):
        reply = await FoundryAnalyst(model, SqlTool(NoLogin())).answer("Dairy margin in October?")
    assert reply == AgentReply(
        answer="Your account doesn't have access to Riad Market's sales data. Sign in with an "
        "account that does.",
        refusal="no_access",
    )
    assert model.calls == []

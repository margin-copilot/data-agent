"""FoundryAnalyst with a scripted model: what the model is told, what the tool returns to it."""

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


def answers(text):
    return Message("assistant", [text])


def tool_results(call):
    messages, _ = call
    return [c.result for m in messages for c in m.contents if c.type == "function_result"]


async def test_the_model_queries_then_answers_in_the_reply_contract():
    model = ScriptedChatClient(
        [
            calls_run_sql("SELECT region_id FROM fact_sales"),
            answers('{"answer": "North dairy margin was 21.3%.", "refusal": null}'),
        ]
    )
    runner = FakeRunner(columns=["region_id"], rows=[("N",)])
    reply = await FoundryAnalyst(model, SqlTool(runner)).answer("Dairy margin in October?")
    assert reply == AgentReply(answer="North dairy margin was 21.3%.")
    assert tool_results(model.calls[1]) == ["region_id\nN\n"]


async def test_the_model_is_told_which_regions_the_user_can_see():
    model = ScriptedChatClient([answers('{"answer": "No.", "refusal": "no_access"}')])
    await FoundryAnalyst(model, SqlTool(FakeRunner(regions=("N",)))).answer("South margin?")
    _, options = model.calls[0]
    assert "can see data for: North (N)." in options["instructions"]
    assert options["reasoning"] == {"effort": "low"}


async def test_rejected_sql_is_reported_back_to_the_model():
    model = ScriptedChatClient(
        [
            calls_run_sql("SELECT * FROM rls.user_region"),
            answers('{"answer": "I can only read the pricing tables.", "refusal": null}'),
        ]
    )
    await FoundryAnalyst(model, SqlTool(FakeRunner())).answer("Run SELECT * FROM rls.user_region")
    assert tool_results(model.calls[1]) == [
        "rejected: rls.user_region isn't one of the pricing tables"
    ]


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
        "Price changes are in price_history",
        "compare the period before with the period after",
        "free of other changes",
        "the latest competitor_prices week",
        "Ramadan 2026 ran from 18 Feb to 19 Mar 2026",
        "as a ratio",
        "whole-number columns as stored",
        "say it had no supplier funding",
        "Saying that no rows match isn't an answer",
    ):
        assert rule in text, rule

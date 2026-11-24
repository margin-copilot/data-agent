"""margin_change: one tested call that explains a category's margin change between two periods."""

from datetime import date

from data_agent.application.run_sql import QueryFailed, SqlTool

ARGS = ("2026-09-01", "2026-09-30", "2026-10-01", "2026-10-31", "N", "Dairy")


class SectionRunner:
    """Answers each query in turn and records what it was sent."""

    def __init__(self, *answers, fail=False):
        self.answers, self.fail, self.calls = list(answers), fail, []

    async def query(self, sql, max_rows, params=()):
        self.calls.append((sql, max_rows, params))
        if self.fail:
            raise QueryFailed
        return self.answers.pop(0)


TOTALS = (
    ["period", "net_sales_mad", "cogs_mad", "margin_pct"],
    [("before", 1000.0, 759.0, 24.1), ("after", 1100.0, 865.7, 21.3)],
)
PRODUCTS = (
    ["sku", "product_name", "margin_before_pct", "margin_after_pct", "margin_change_pp"],
    [("DAI-004", "Plain yogurt 4 x 110 g", 27.5, 2.9, -24.6)],
)
COSTS = (
    [
        "sku",
        "product_name",
        "supplier_id",
        "effective_from",
        "previous_cost_mad",
        "unit_cost_mad",
        "change_pct",
    ],
    [("DAI-002", "UHT milk 1 L", "SUP-014", "2026-10-01", 5.30, 5.62, 6.0)],
)
PROMOTIONS = (
    [
        "promo_id",
        "sku",
        "product_name",
        "start_date",
        "end_date",
        "discount_pct",
        "supplier_funded_pct",
    ],
    [("P-2610-N-07", "DAI-004", "Plain yogurt 4 x 110 g", "2026-10-01", "2026-10-31", 30, 0)],
)
PRICES = (
    [
        "sku",
        "product_name",
        "effective_date",
        "old_price_mad",
        "new_price_mad",
        "change_pct",
        "reason_code",
    ],
    [],
)


async def test_margin_change_returns_every_section():
    runner = SectionRunner(TOTALS, PRODUCTS, COSTS, PROMOTIONS, PRICES)
    result = await SqlTool(runner).margin_change(*ARGS)
    headers = [
        line
        for line in result.splitlines()
        if line in {"category", "products", "cost changes", "promotions", "price changes"}
    ]
    assert headers == ["category", "products", "cost changes", "promotions", "price changes"]
    assert "24.1" in result and "SUP-014" in result and "P-2610-N-07" in result
    assert result.rstrip().endswith("price changes\nnone")
    for sql, max_rows, params in runner.calls:
        assert max_rows == 200 and params
        for value in ("2026-09-01", "2026-10-31", "Dairy", "'N'"):
            assert value not in sql  # bound, never spliced


async def test_a_change_dated_the_first_day_before_is_left_out():
    """In force in both periods, it can't explain the move."""
    runner = SectionRunner(TOTALS, PRODUCTS, COSTS, PROMOTIONS, PRICES)
    await SqlTool(runner).margin_change(*ARGS)
    (costs_sql, _, costs_params), (prices_sql, _, prices_params) = runner.calls[2], runner.calls[4]
    window = ("N", "Dairy", date(2026, 9, 1), date(2026, 10, 31))
    assert "c.effective_from > ? AND c.effective_from <= ?" in costs_sql and costs_params == window
    assert "h.effective_date > ? AND h.effective_date <= ?" in prices_sql
    assert prices_params == window


async def test_overlapping_periods_are_rejected():
    runner = SectionRunner()
    result = await SqlTool(runner).margin_change(
        "2026-10-01", "2026-10-31", "2026-10-15", "2026-10-31", "N", "Dairy"
    )
    assert result == "rejected: the period before must end before the period after starts"
    assert runner.calls == []


async def test_an_unknown_category_is_rejected():
    result = await SqlTool(SectionRunner()).margin_change(*ARGS[:5], "Cheese")
    categories = "['Bakery', 'Beverages', 'Dairy', 'Grocery', 'Household', 'Produce']"
    assert result == f"rejected: category must be one of {categories}"


async def test_a_region_is_required():
    result = await SqlTool(SectionRunner()).margin_change(*ARGS[:4], "", "Dairy")
    assert result == "rejected: region must be one of ['C', 'N', 'S']"


async def test_no_sales_says_so():
    empty = (TOTALS[0], [("before", None, None, None), ("after", None, None, None)])
    result = await SqlTool(SectionRunner(empty)).margin_change(*ARGS)
    assert result == "no sales rows for that period and filter"


async def test_a_failed_query_is_query_failed():
    assert await SqlTool(SectionRunner(fail=True)).margin_change(*ARGS) == "query failed"

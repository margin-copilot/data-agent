"""Test doubles: a scripted SQL runner."""

from data_agent.application.run_sql import VISIBLE_REGIONS, QueryFailed


class FakeRunner:
    def __init__(self, regions=("N",), columns=("n",), rows=((1,),), fail=False) -> None:
        self.regions, self.columns, self.rows, self.fail = regions, list(columns), list(rows), fail
        self.queries: list[tuple[str, int]] = []

    async def query(self, sql: str, max_rows: int) -> tuple[list[str], list[tuple]]:
        self.queries.append((sql, max_rows))
        if sql == VISIBLE_REGIONS:
            return ["region_id"], [(region,) for region in self.regions]
        if self.fail:
            raise QueryFailed
        return self.columns, self.rows[:max_rows]

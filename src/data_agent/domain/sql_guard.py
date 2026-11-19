"""The SELECT guard for run_sql: one plain SELECT over the pricing tables, nothing else. Region
security stays the database's job (RLS on the user's own token); this keeps the model to reads."""

import sqlglot
from sqlglot import exp
from sqlglot.errors import SqlglotError

TABLES = frozenset(
    {
        "dim_region",
        "dim_store",
        "dim_product",
        "fact_sales",
        "supplier_costs",
        "promotions",
        "price_history",
        "competitor_prices",
    }
)
FORBIDDEN = (exp.Into, exp.Lateral, exp.Insert, exp.Update, exp.Delete, exp.Merge, exp.Command)


class SqlRejected(ValueError):
    pass


def check_select(sql: str) -> None:
    try:
        statements = [s for s in sqlglot.parse(sql, dialect="tsql") if s is not None]
    except SqlglotError:
        raise SqlRejected("the SQL couldn't be parsed") from None
    if len(statements) != 1:
        raise SqlRejected("send exactly one statement")
    tree = statements[0]
    if not isinstance(tree, exp.Select | exp.SetOperation):
        raise SqlRejected("only SELECT is allowed")
    top_with = tree.args.get("with_")
    for node in tree.walk():
        if isinstance(node, FORBIDDEN):
            raise SqlRejected("only plain SELECT is allowed (no INTO, APPLY or writes)")
        if isinstance(node, exp.With) and node is not top_with:
            raise SqlRejected("WITH is allowed only at the start")
        if isinstance(node, exp.Dot) and isinstance(node.expression, exp.Func):
            raise SqlRejected("schema-qualified functions aren't allowed")
    ctes = list(top_with.expressions) if top_with else []
    names = [cte.alias_or_name.lower() for cte in ctes]
    for table in tree.find_all(exp.Table):
        identifier = table.this
        if not isinstance(identifier, exp.Identifier) or table.catalog:
            raise SqlRejected(f"{table.sql('tsql')} isn't a table you can query")
        if identifier.args.get("temporary") or identifier.args.get("global_"):
            raise SqlRejected("temporary tables aren't allowed")
        name, schema = identifier.name.lower(), (table.db or "dbo").lower()
        # A CTE body sees itself (recursion) and the CTEs before it, never later ones.
        enclosing = table.find_ancestor(exp.CTE)
        position = next((i for i, cte in enumerate(ctes) if cte is enclosing), len(ctes) - 1)
        if not table.db and name in names[: position + 1]:
            continue
        if schema != "dbo" or name not in TABLES:
            raise SqlRejected(f"{schema}.{name} isn't one of the pricing tables")

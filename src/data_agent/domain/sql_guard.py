"""The SELECT guard for run_sql: one plain SELECT over the pricing tables, nothing else. Region
security stays the database's job (RLS on the user's own token); this keeps the model to reads."""

import re
from datetime import date

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
DATE = re.compile(r"\d{4}-\d{2}-\d{2}")
# Whole-number columns: T-SQL averages and divides them as integers, silently dropping the
# fraction (29.83 units becomes 29, a 2.19 ratio becomes 2).
WHOLE_NUMBER_COLUMNS = frozenset({"units", "sales_area_m2", "discount_pct", "supplier_funded_pct"})
TRUNCATES = (
    "AVG or / over whole numbers truncates in T-SQL; cast first, "
    "for example AVG(CAST(units AS decimal(18, 4))) or SUM(units) * 1.0 / COUNT(*)"
)
# T-SQL reserved keywords (Learn: Reserved keywords, Transact-SQL). SQL Server never accepts them
# unquoted as names, but sqlglot can read one as an alias: "FROM t EXEC('...')" would pass as a
# SELECT while SQL Server runs a second statement.
RESERVED = frozenset(
    """
    ADD ALL ALTER AND ANY AS ASC AUTHORIZATION BACKUP BEGIN BETWEEN BREAK BROWSE BULK BY
    CASCADE CASE CHECK CHECKPOINT CLOSE CLUSTERED COALESCE COLLATE COLUMN COMMIT COMPUTE
    CONSTRAINT CONTAINS CONTAINSTABLE CONTINUE CONVERT CREATE CROSS CURRENT CURRENT_DATE
    CURRENT_TIME CURRENT_TIMESTAMP CURRENT_USER CURSOR DATABASE DBCC DEALLOCATE DECLARE
    DEFAULT DELETE DENY DESC DISK DISTINCT DISTRIBUTED DOUBLE DROP DUMP ELSE END ERRLVL
    ESCAPE EXCEPT EXEC EXECUTE EXISTS EXIT EXTERNAL FETCH FILE FILLFACTOR FOR FOREIGN
    FREETEXT FREETEXTTABLE FROM FULL FUNCTION GOTO GRANT GROUP HAVING HOLDLOCK IDENTITY
    IDENTITY_INSERT IDENTITYCOL IF IN INDEX INNER INSERT INTERSECT INTO IS JOIN KEY KILL
    LABEL LEFT LIKE LINENO LOAD MERGE NATIONAL NOCHECK NONCLUSTERED NOT NULL NULLIF OF OFF
    OFFSETS ON OPEN OPENDATASOURCE OPENQUERY OPENROWSET OPENXML OPTION OR ORDER OUTER OVER
    PERCENT PIVOT PLAN PRECISION PRIMARY PRINT PROC PROCEDURE PUBLIC RAISERROR READ READTEXT
    RECONFIGURE REFERENCES REPLICATION RESTORE RESTRICT RETURN REVERT REVOKE RIGHT ROLLBACK
    ROWCOUNT ROWGUIDCOL RULE SAVE SCHEMA SECURITYAUDIT SELECT SEMANTICKEYPHRASETABLE
    SEMANTICSIMILARITYDETAILSTABLE SEMANTICSIMILARITYTABLE SESSION_USER SET SETUSER SHUTDOWN
    SOME STATISTICS SYSTEM_USER TABLE TABLESAMPLE TEXTSIZE THEN TO TOP TRAN TRANSACTION
    TRIGGER TRUNCATE TRY_CONVERT TSEQUAL UNION UNIQUE UNPIVOT UPDATE UPDATETEXT USE USER
    VALUES VARYING VIEW WAITFOR WHEN WHERE WHILE WITH WRITETEXT
    """.split()
) | {"THROW", "RECEIVE", "SEND"}  # statement keywords that aren't on the reserved list


class SqlRejected(ValueError):
    pass


def _whole(node: exp.Expression) -> bool:
    """True when T-SQL would compute this as an integer: a whole-number column, a count, or a
    sum, minimum or maximum of one."""
    while isinstance(node, exp.Paren):
        node = node.this
    if isinstance(node, exp.Column):
        return node.name.lower() in WHOLE_NUMBER_COLUMNS
    if isinstance(node, exp.Count):
        return True
    if isinstance(node, exp.Sum | exp.Min | exp.Max):
        return _whole(node.this)
    return False


def check_select(sql: str) -> None:
    if any((ord(c) < 32 and c not in "\t\n\r") or c in "\x85\u2028\u2029" for c in sql):
        raise SqlRejected("control characters aren't allowed")
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
        if node.comments:  # where a comment ends can differ between sqlglot and SQL Server
            raise SqlRejected("comments aren't allowed")
        if isinstance(node, exp.Identifier) and not node.quoted and node.name.upper() in RESERVED:
            raise SqlRejected(
                f"{node.name.upper()} is a T-SQL keyword; quote it or use another name"
            )
        if isinstance(node, exp.TableAlias) and node.args.get("columns"):
            raise SqlRejected("table alias column lists aren't allowed")
        if isinstance(node, FORBIDDEN):
            raise SqlRejected("only plain SELECT is allowed (no INTO, APPLY or writes)")
        if isinstance(node, exp.With) and node is not top_with:
            raise SqlRejected("WITH is allowed only at the start")
        if isinstance(node, exp.Avg) and _whole(node.this):
            raise SqlRejected(TRUNCATES)
        if isinstance(node, exp.Div) and _whole(node.this) and _whole(node.expression):
            raise SqlRejected(TRUNCATES)
        if isinstance(node, exp.Dot) and isinstance(node.expression, exp.Func):
            raise SqlRejected("schema-qualified functions aren't allowed")
        # SQL Server reports an impossible date only while reading rows, as a hidden error.
        if isinstance(node, exp.Literal) and node.is_string and DATE.fullmatch(node.this):
            try:
                date.fromisoformat(node.this)
            except ValueError:
                raise SqlRejected(f"{node.this} isn't a real date") from None
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

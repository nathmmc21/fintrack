from contextlib import contextmanager

from psycopg import sql
from psycopg.rows import dict_row
from psycopg_pool import ConnectionPool

from .config import settings

pool = ConnectionPool(
    settings.database_url, min_size=1, max_size=10, open=False,
    kwargs={"row_factory": dict_row},
)


@contextmanager
def db():
    """One transaction per `with` block: commits on success, rolls back on ANY exception
    (including HTTPException), so half-finished writes never persist."""
    with pool.connection() as conn:
        with conn.cursor() as cur:
            yield cur


def update_sql(table: str, data: dict, where: str, where_params: tuple, returning: str = "id"):
    """Build a parameterised UPDATE.

    `table`, the keys of `data` and `where`/`returning` always come from our own code (pydantic
    model fields / string constants), never from the request, and are quoted with sql.Identifier.
    All VALUES are bound parameters, so user input can never alter the statement.
    """
    sets = sql.SQL(", ").join(sql.SQL("{} = %s").format(sql.Identifier(c)) for c in data)
    query = (
        sql.SQL("UPDATE {t} SET ").format(t=sql.Identifier(table))
        + sets
        + sql.SQL(" WHERE " + where + " RETURNING " + returning)
    )
    return query, (*data.values(), *where_params)


def escape_like(text: str) -> str:
    return text.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")

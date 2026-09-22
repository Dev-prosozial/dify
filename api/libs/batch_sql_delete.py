import logging
from collections.abc import Callable
from typing import Any

import click
import sqlalchemy as sa

logger = logging.getLogger(__name__)


def batch_delete_by_query(
    query_sql: str,
    params: dict[str, Any],
    delete_func: Callable,
    name: str,
    *,
    session_factory: Callable,
) -> None:
    """Delete records in batches by a raw SQL id-selection query.

    Repeatedly executes ``query_sql`` (expected to return rows with an ``id``
    column, ``LIMIT``-ed) and calls ``delete_func(session, record_id)`` for each
    row, committing after each successful record so a single failure does not
    roll back the rest of the batch.

    Args:
        query_sql: Raw SQL selecting the ids to delete (should be bounded with ``LIMIT``).
        params: Bound parameters for ``query_sql``.
        delete_func: Callable receiving ``(session, record_id)`` that performs the deletion.
        name: Human-readable name of the record type for logging.
        session_factory: Callable returning a new ``Session`` for each batch.
    """
    while True:
        with session_factory() as session:
            rs = session.execute(sa.text(query_sql), params)
            rows = rs.fetchall()
            if not rows:
                break

            success_count = 0
            for i in rows:
                record_id = str(i.id)
                try:
                    delete_func(session, record_id)
                    logger.info(click.style(f"Deleted {name} {record_id}", fg="green"))
                    session.commit()
                    success_count += 1
                except Exception:
                    logger.exception("Error occurred while deleting %s %s", name, record_id)
                    # continue with next record even if one deletion fails
                    session.rollback()
                    continue

            rs.close()

            # If we couldn't delete ANY records in this batch, we must break out
            # of the while loop to prevent an infinite loop where we keep
            # fetching the same failing records.
            if success_count == 0:
                logger.warning(
                    click.style(
                        f"Failed to delete any {name} in the current batch. Stopping to prevent infinite loop.",
                        fg="yellow",
                    )
                )
                break

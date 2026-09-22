from __future__ import annotations

from unittest.mock import MagicMock

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from core.db.session_factory import session_factory
from libs.batch_sql_delete import batch_delete_by_query
from models import EndUser
from models.enums import EndUserType


def _batch_delete(query_sql: str, params: dict, delete_func, name: str) -> None:
    batch_delete_by_query(query_sql, params, delete_func, name, session_factory=session_factory.create_session)


def _end_user(tenant_id: str, app_id: str, session_id: str) -> EndUser:
    return EndUser(
        tenant_id=tenant_id,
        app_id=app_id,
        type=EndUserType.SERVICE_API,
        external_user_id=session_id,
        session_id=session_id,
    )


def test_batch_delete_by_query_deletes_all_batches(sqlite_session: Session) -> None:
    tenant_id = "tenant-1"
    app_id = "app-1"
    users = [_end_user(tenant_id, app_id, f"user-{i}") for i in range(5)]
    sqlite_session.add_all(users)
    sqlite_session.commit()
    user_ids = {u.id for u in users}

    def delete_one(session, record_id: str) -> None:
        session.execute(delete(EndUser).where(EndUser.id == record_id))

    _batch_delete(
        "select id from end_users where app_id = :app_id limit 1000",
        {"app_id": app_id},
        delete_one,
        "end user",
    )

    sqlite_session.expire_all()
    remaining = list(sqlite_session.scalars(select(EndUser).where(EndUser.app_id == app_id)).all())
    assert remaining == []


def test_batch_delete_by_query_does_not_touch_other_scope(sqlite_session: Session) -> None:
    tenant_id = "tenant-1"
    app_id = "app-1"
    target = _end_user(tenant_id, app_id, "user-target")
    other = _end_user("tenant-other", app_id, "user-other")
    sqlite_session.add_all([target, other])
    sqlite_session.commit()
    other_id = other.id

    def delete_one(session, record_id: str) -> None:
        session.execute(delete(EndUser).where(EndUser.id == record_id))

    _batch_delete(
        "select id from end_users where app_id = :app_id and tenant_id = :tenant_id limit 1000",
        {"app_id": app_id, "tenant_id": tenant_id},
        delete_one,
        "end user",
    )

    sqlite_session.expunge_all()
    assert sqlite_session.get(EndUser, other_id) is not None


def test_batch_delete_by_query_stops_on_zero_success(sqlite_session: Session, caplog) -> None:
    tenant_id = "tenant-1"
    app_id = "app-1"
    user = _end_user(tenant_id, app_id, "user-1")
    sqlite_session.add(user)
    sqlite_session.commit()

    def always_fail(session, record_id: str) -> None:  # noqa: ARG001
        raise RuntimeError("cannot delete")

    _batch_delete(
        "select id from end_users where app_id = :app_id limit 1000",
        {"app_id": app_id},
        always_fail,
        "end user",
    )

    sqlite_session.expire_all()
    assert sqlite_session.get(EndUser, user.id) is not None
    assert any("Stopping to prevent infinite loop" in record.message for record in caplog.records)


def test_batch_delete_by_query_no_rows_returns_immediately() -> None:
    delete_func = MagicMock()
    _batch_delete(
        "select id from end_users where app_id = :app_id limit 1000",
        {"app_id": "no-such-app"},
        delete_func,
        "end user",
    )
    delete_func.assert_not_called()

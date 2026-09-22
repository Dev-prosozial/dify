from __future__ import annotations

from unittest.mock import MagicMock
from uuid import uuid4

from pytest_mock import MockerFixture

from models.enums import CreatorUserRole, EndUserType
from models.model import App, EndUser
from services.end_user_service import EndUserDataSummary, EndUserDeletionResult, EndUserService


class TestEndUserServiceLookup:
    def test_get_end_user_by_external_id_matches_by_session_id(self, mocker: MockerFixture) -> None:
        end_user = EndUser(
            id=str(uuid4()),
            tenant_id="tenant-1",
            app_id="app-1",
            type=EndUserType.SERVICE_API,
            external_user_id="external-1",
            session_id="external-1",
        )

        session = MagicMock()
        session.scalar.return_value = end_user

        def begin():
            return _BeginCtx(session)

        mocker.patch("services.end_user_service.db", MagicMock())
        mocker.patch(
            "services.end_user_service.sessionmaker",
            return_value=MagicMock(begin=begin),
        )

        result = EndUserService.get_end_user_by_external_id(
            tenant_id="tenant-1", app_id="app-1", external_user_id="external-1"
        )

        assert result is end_user
        call = session.scalar.call_args.args[0]
        statement = str(call)
        assert "end_users" in statement
        assert "tenant_id" in statement
        assert "app_id" in statement
        assert "session_id" in statement

    def test_get_end_user_by_external_id_returns_none(self, mocker: MockerFixture) -> None:
        session = MagicMock()
        session.scalar.return_value = None

        def begin():
            return _BeginCtx(session)

        mocker.patch("services.end_user_service.db", MagicMock())
        mocker.patch(
            "services.end_user_service.sessionmaker",
            return_value=MagicMock(begin=begin),
        )

        result = EndUserService.get_end_user_by_external_id(
            tenant_id="tenant-1", app_id="app-1", external_user_id="missing"
        )

        assert result is None


class TestEndUserServiceDataSummary:
    def test_get_data_summary_counts_all_three(self, mocker: MockerFixture) -> None:
        app = App(id="app-1", tenant_id="tenant-1")
        end_user = EndUser(
            id="end-user-1",
            tenant_id="tenant-1",
            app_id="app-1",
            type=EndUserType.SERVICE_API,
            external_user_id="external-1",
            session_id="external-1",
        )

        session = MagicMock()
        # conversation_count, message_count, upload_file_count
        session.scalar.side_effect = [5, 42, 3]

        def begin():
            return _BeginCtx(session)

        mocker.patch("services.end_user_service.db", MagicMock())
        mocker.patch(
            "services.end_user_service.sessionmaker",
            return_value=MagicMock(begin=begin),
        )

        result = EndUserService.get_data_summary(app, end_user)

        assert result == EndUserDataSummary(conversation_count=5, message_count=42, upload_file_count=3)
        statements = [str(call.args[0]) for call in session.scalar.call_args_list]
        assert any("conversations" in st for st in statements)
        assert any("messages" in st for st in statements)
        assert any("upload_files" in st for st in statements)
        assert any(CreatorUserRole.END_USER.value in st for st in statements)

    def test_get_data_summary_treats_none_as_zero(self, mocker: MockerFixture) -> None:
        app = App(id="app-1", tenant_id="tenant-1")
        end_user = EndUser(
            id="end-user-1",
            tenant_id="tenant-1",
            app_id="app-1",
            type=EndUserType.SERVICE_API,
            external_user_id="external-1",
            session_id="external-1",
        )

        session = MagicMock()
        session.scalar.side_effect = [None, None, None]

        def begin():
            return _BeginCtx(session)

        mocker.patch("services.end_user_service.db", MagicMock())
        mocker.patch(
            "services.end_user_service.sessionmaker",
            return_value=MagicMock(begin=begin),
        )

        result = EndUserService.get_data_summary(app, end_user)

        assert result == EndUserDataSummary(conversation_count=0, message_count=0, upload_file_count=0)


class TestEndUserServiceDelete:
    def test_delete_all_data_dispatches_cleanup_below_threshold(self, mocker: MockerFixture) -> None:
        app = App(id="app-1", tenant_id="tenant-1")
        end_user = EndUser(
            id="end-user-1",
            tenant_id="tenant-1",
            app_id="app-1",
            type=EndUserType.SERVICE_API,
            external_user_id="external-1",
            session_id="external-1",
        )

        session = MagicMock()
        session.execute.return_value = [("c1",), ("c2",)]

        def begin():
            return _BeginCtx(session)

        dispatch = mocker.patch.object(EndUserService, "_dispatch_conversation_cleanup")
        upload_task = mocker.patch("services.end_user_service.delete_end_user_upload_files")

        mocker.patch("services.end_user_service.db", MagicMock())
        mocker.patch(
            "services.end_user_service.sessionmaker",
            return_value=MagicMock(begin=begin),
        )

        result = EndUserService.delete_all_data(app, end_user)

        assert result == EndUserDeletionResult(conversations_marked=2)
        dispatch.assert_called_once_with(["c1", "c2"])
        upload_task.delay.assert_called_once_with(app.tenant_id, end_user.id)
        statement = str(session.execute.call_args.args[0])
        assert "conversations" in statement

    def test_delete_all_data_enqueues_upload_task(self, mocker: MockerFixture) -> None:
        app = App(id="app-1", tenant_id="tenant-1")
        end_user = EndUser(
            id="end-user-1",
            tenant_id="tenant-1",
            app_id="app-1",
            type=EndUserType.SERVICE_API,
            external_user_id="external-1",
            session_id="external-1",
        )

        session = MagicMock()
        session.execute.return_value = []

        def begin():
            return _BeginCtx(session)

        mocker.patch.object(EndUserService, "_dispatch_conversation_cleanup")
        upload_task = mocker.patch("services.end_user_service.delete_end_user_upload_files")
        mocker.patch("services.end_user_service.db", MagicMock())
        mocker.patch(
            "services.end_user_service.sessionmaker",
            return_value=MagicMock(begin=begin),
        )

        result = EndUserService.delete_all_data(app, end_user)

        assert result.conversations_marked == 0
        upload_task.delay.assert_called_once_with(app.tenant_id, end_user.id)

    def test_dispatch_skips_when_above_threshold(self, mocker: MockerFixture) -> None:
        conversation_task = mocker.patch("services.end_user_service.delete_conversation_related_data")
        many_ids = [f"c-{i}" for i in range(201)]

        EndUserService._dispatch_conversation_cleanup(many_ids)

        conversation_task.delay.assert_not_called()

    def test_dispatch_enqueues_each_below_threshold(self, mocker: MockerFixture) -> None:
        conversation_task = mocker.patch("services.end_user_service.delete_conversation_related_data")
        ids = ["c1", "c2", "c3"]

        EndUserService._dispatch_conversation_cleanup(ids)

        assert conversation_task.delay.call_count == 3
        conversation_task.delay.assert_has_calls([mocker.call("c1"), mocker.call("c2"), mocker.call("c3")])

    def test_dispatch_tolerates_enqueue_failure(self, mocker: MockerFixture) -> None:
        conversation_task = mocker.patch("services.end_user_service.delete_conversation_related_data")
        conversation_task.delay.side_effect = RuntimeError("broker down")

        EndUserService._dispatch_conversation_cleanup(["c1", "c2"])

        assert conversation_task.delay.call_count == 2


class _BeginCtx:
    def __init__(self, session: MagicMock):
        self._session = session

    def __enter__(self):
        return self._session

    def __exit__(self, *exc):
        return False

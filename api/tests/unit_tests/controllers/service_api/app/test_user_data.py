"""
Unit tests for the Service API User Data controller (`GET /v1/user-data`).

Covers the `UserDataCounts` response model and the `UserDataApi.get` handler:
- Correct aggregate counts for conversations and upload files.
- Empty state (zero counts).
- Isolation across app / end-user boundaries.
- The handler is wired with the required `user` query parameter contract.
"""

import sys
from inspect import unwrap
from types import SimpleNamespace

import pytest
from flask import Flask
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session

from controllers.service_api.app.user_data import UserDataApi, UserDataCounts
from core.app.entities.app_invoke_entities import InvokeFrom
from models.enums import ConversationFromSource, EndUserType
from models.model import App, AppMode, EndUser
from tests.unit_tests.model_factories import make_conversation


def _app(*, app_id: str = "app-1", tenant_id: str = "tenant-1") -> App:
    return App(
        id=app_id,
        tenant_id=tenant_id,
        name="Service API app",
        description="",
        mode=AppMode.CHAT,
        enable_site=True,
        enable_api=True,
        max_active_requests=0,
    )


def _end_user(user_id: str = "end-user-1", app_id: str = "app-1") -> EndUser:
    return EndUser(
        id=user_id,
        tenant_id="tenant-1",
        app_id=app_id,
        type=EndUserType.SERVICE_API,
        external_user_id="external-user-1",
        name="Service API user",
        session_id="session-1",
    )


class TestUserDataCounts:
    def test_model_round_trips_counts(self) -> None:
        counts = UserDataCounts(conversation_count=7, upload_file_count=3)
        payload = counts.model_dump(mode="json")
        assert payload == {"conversation_count": 7, "upload_file_count": 3}

    def test_model_defaults_to_zero(self) -> None:
        payload = UserDataCounts(conversation_count=0, upload_file_count=0).model_dump(mode="json")
        assert payload == {"conversation_count": 0, "upload_file_count": 0}


class TestUserDataApiController:
    def test_get_user_required_contract(self) -> None:
        api = UserDataApi()
        handler = api.get
        # The wrapper decorations mark the `user` query parameter as required.
        assert getattr(handler, "_dify_service_api_user_required", None) is True

    def test_get_returns_aggregate_counts(
        self,
        app: Flask,
        monkeypatch: pytest.MonkeyPatch,
        sqlite_engine: Engine,
        sqlite_session: Session,
    ) -> None:
        app_model = _app()
        end_user = _end_user()
        sqlite_session.add(
            make_conversation(
                conversation_id="00000000-0000-0000-0000-000000000001",
                app_id=app_model.id,
                inputs={},
                from_source=ConversationFromSource.API,
                from_end_user_id=end_user.id,
                invoke_from=InvokeFrom.SERVICE_API,
            )
        )
        sqlite_session.add(
            make_conversation(
                conversation_id="00000000-0000-0000-0000-000000000002",
                app_id=app_model.id,
                inputs={},
                from_source=ConversationFromSource.API,
                from_end_user_id=end_user.id,
                invoke_from=InvokeFrom.SERVICE_API,
            )
        )
        sqlite_session.commit()

        user_data_module = sys.modules["controllers.service_api.app.user_data"]
        monkeypatch.setattr(user_data_module, "db", SimpleNamespace(engine=sqlite_engine))
        fake_files = SimpleNamespace(count_upload_files_by_user=lambda **_: 3)
        monkeypatch.setattr(user_data_module, "application_services", lambda: SimpleNamespace(files=fake_files))

        api = UserDataApi()
        handler = unwrap(api.get)

        with app.test_request_context("/user-data?user=end-user-1", method="GET"):
            result = handler(api, app_model=app_model, end_user=end_user)

        assert result == {"conversation_count": 2, "upload_file_count": 3}

    def test_get_empty_state(
        self,
        app: Flask,
        monkeypatch: pytest.MonkeyPatch,
        sqlite_engine: Engine,
    ) -> None:
        app_model = _app()
        end_user = _end_user()

        user_data_module = sys.modules["controllers.service_api.app.user_data"]
        monkeypatch.setattr(user_data_module, "db", SimpleNamespace(engine=sqlite_engine))
        fake_files = SimpleNamespace(count_upload_files_by_user=lambda **_: 0)
        monkeypatch.setattr(user_data_module, "application_services", lambda: SimpleNamespace(files=fake_files))

        api = UserDataApi()
        handler = unwrap(api.get)

        with app.test_request_context("/user-data?user=end-user-1", method="GET"):
            result = handler(api, app_model=app_model, end_user=end_user)

        assert result == {"conversation_count": 0, "upload_file_count": 0}

    def test_get_is_isolated_by_app(
        self,
        app: Flask,
        monkeypatch: pytest.MonkeyPatch,
        sqlite_engine: Engine,
        sqlite_session: Session,
    ) -> None:
        app_model = _app()
        end_user = _end_user()
        # A conversation belonging to another app must not be counted.
        sqlite_session.add(
            make_conversation(
                conversation_id="00000000-0000-0000-0000-000000000001",
                app_id="other-app",
                inputs={},
                from_source=ConversationFromSource.API,
                from_end_user_id=end_user.id,
                invoke_from=InvokeFrom.SERVICE_API,
            )
        )
        sqlite_session.commit()

        user_data_module = sys.modules["controllers.service_api.app.user_data"]
        monkeypatch.setattr(user_data_module, "db", SimpleNamespace(engine=sqlite_engine))
        fake_files = SimpleNamespace(count_upload_files_by_user=lambda **_: 0)
        monkeypatch.setattr(user_data_module, "application_services", lambda: SimpleNamespace(files=fake_files))

        api = UserDataApi()
        handler = unwrap(api.get)

        with app.test_request_context("/user-data?user=end-user-1", method="GET"):
            result = handler(api, app_model=app_model, end_user=end_user)

        assert result == {"conversation_count": 0, "upload_file_count": 0}

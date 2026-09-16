from datetime import UTC, datetime
from uuid import UUID, uuid4

import pytest
from flask import Flask
from pytest_mock import MockerFixture

from controllers.service_api.end_user.end_user import EndUserApi, EndUserSummaryApi
from controllers.service_api.end_user.error import EndUserNotFoundError
from models.enums import EndUserType
from models.model import App, EndUser
from services.end_user_service import EndUserDataSummary


class TestEndUserApi:
    @pytest.fixture
    def resource(self) -> EndUserApi:
        return EndUserApi()

    @pytest.fixture
    def app_model(self) -> App:
        app = App(
            id=str(uuid4()),
            tenant_id=str(uuid4()),
        )
        return app

    def test_get_end_user_returns_all_attributes(
        self, mocker: MockerFixture, resource: EndUserApi, app_model: App
    ) -> None:
        end_user = EndUser(
            id=str(uuid4()),
            tenant_id=app_model.tenant_id,
            app_id=app_model.id,
            type=EndUserType.SERVICE_API,
            external_user_id="external-123",
            name="Alice",
            _is_anonymous=True,
            session_id="session-xyz",
            created_at=datetime(2024, 1, 1, tzinfo=UTC),
            updated_at=datetime(2024, 1, 2, tzinfo=UTC),
        )

        get_end_user_by_id = mocker.patch(
            "controllers.service_api.end_user.end_user.EndUserService.get_end_user_by_id", return_value=end_user
        )

        result = EndUserApi.get.__wrapped__(resource, app_model=app_model, end_user_id=UUID(end_user.id))

        get_end_user_by_id.assert_called_once_with(
            tenant_id=app_model.tenant_id, app_id=app_model.id, end_user_id=end_user.id
        )
        assert result["id"] == end_user.id
        assert result["tenant_id"] == end_user.tenant_id
        assert result["app_id"] == end_user.app_id
        assert result["type"] == end_user.type
        assert result["external_user_id"] == end_user.external_user_id
        assert result["name"] == end_user.name
        assert result["is_anonymous"] is True
        assert result["session_id"] == end_user.session_id
        assert result["created_at"].startswith("2024-01-01T00:00:00")
        assert result["updated_at"].startswith("2024-01-02T00:00:00")

    def test_get_end_user_not_found(self, mocker: MockerFixture, resource: EndUserApi, app_model: App) -> None:
        mocker.patch("controllers.service_api.end_user.end_user.EndUserService.get_end_user_by_id", return_value=None)

        with pytest.raises(EndUserNotFoundError):
            EndUserApi.get.__wrapped__(resource, app_model=app_model, end_user_id=uuid4())


class TestEndUserSummaryApi:
    @pytest.fixture
    def resource(self) -> EndUserSummaryApi:
        return EndUserSummaryApi()

    @pytest.fixture
    def app_model(self) -> App:
        app = App(
            id=str(uuid4()),
            tenant_id=str(uuid4()),
        )
        return app

    def test_get_data_summary_returns_counts(
        self, mocker: MockerFixture, resource: EndUserSummaryApi, app_model: App
    ) -> None:
        end_user = EndUser(
            id=str(uuid4()),
            tenant_id=app_model.tenant_id,
            app_id=app_model.id,
            type=EndUserType.SERVICE_API,
            external_user_id="external-123",
            name="Alice",
            _is_anonymous=True,
            session_id="external-123",
        )

        get_end_user = mocker.patch(
            "controllers.service_api.end_user.end_user.EndUserService.get_end_user_by_external_id",
            return_value=end_user,
        )
        get_summary = mocker.patch(
            "controllers.service_api.end_user.end_user.EndUserService.get_data_summary",
            return_value=EndUserDataSummary(conversation_count=5, message_count=42, upload_file_count=3),
        )

        app = Flask(__name__)
        with app.test_request_context("/end-users", query_string={"user": "external-123"}):
            result = EndUserSummaryApi.get.__wrapped__(resource, app_model=app_model)

        get_end_user.assert_called_once_with(
            tenant_id=app_model.tenant_id, app_id=app_model.id, external_user_id="external-123"
        )
        get_summary.assert_called_once_with(app_model, end_user)
        assert result == {
            "end_user_id": end_user.id,
            "conversation_count": 5,
            "message_count": 42,
            "upload_file_count": 3,
        }

    def test_get_data_summary_not_found(
        self, mocker: MockerFixture, resource: EndUserSummaryApi, app_model: App
    ) -> None:
        mocker.patch(
            "controllers.service_api.end_user.end_user.EndUserService.get_end_user_by_external_id", return_value=None
        )

        app = Flask(__name__)
        with app.test_request_context("/end-users", query_string={"user": "missing-user"}):
            with pytest.raises(EndUserNotFoundError):
                EndUserSummaryApi.get.__wrapped__(resource, app_model=app_model)

    def test_get_data_summary_missing_user_raises_bad_request(
        self, resource: EndUserSummaryApi, app_model: App
    ) -> None:
        from werkzeug.exceptions import BadRequest

        app = Flask(__name__)
        with app.test_request_context("/end-users"):
            with pytest.raises(BadRequest):
                EndUserSummaryApi.get.__wrapped__(resource, app_model=app_model)

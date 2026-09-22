from uuid import UUID

from flask import request
from flask_restx import Resource
from werkzeug.exceptions import BadRequest

from controllers.common.schema import register_response_schema_models
from controllers.service_api import service_api_ns
from controllers.service_api.end_user.error import EndUserNotFoundError
from controllers.service_api.wraps import validate_app_token
from fields.end_user_fields import EndUserDataSummaryResponse, EndUserDeletionResponse, EndUserDetail
from models.model import App, DefaultEndUserSessionID
from services.end_user_service import EndUserService

register_response_schema_models(service_api_ns, EndUserDetail, EndUserDataSummaryResponse, EndUserDeletionResponse)


@service_api_ns.route("/end-users/<uuid:end_user_id>")
class EndUserApi(Resource):
    """Resource for retrieving end user details by ID."""

    @service_api_ns.doc(
        summary="Get End User Info",
        description=(
            "Retrieve an end user by ID. Useful when other APIs return an end-user ID (e.g., "
            "`created_by` from [Upload File](/api-reference/files/upload-file))."
        ),
        tags=["End Users"],
        responses={
            200: "End user retrieved successfully.",
            404: "`end_user_not_found` : End user not found.",
        },
    )
    @service_api_ns.doc("get_end_user")
    @service_api_ns.doc(description="Get an end user by ID")
    @service_api_ns.doc(
        params={"end_user_id": "End user ID"},
        responses={
            200: "End user retrieved successfully",
            401: "Unauthorized - invalid API token",
            404: "End user not found",
        },
    )
    @service_api_ns.response(200, "End user retrieved successfully", service_api_ns.models[EndUserDetail.__name__])
    @validate_app_token
    def get(self, app_model: App, end_user_id: UUID):
        """Get end user detail.

        This endpoint is scoped to the current app token's tenant/app to prevent
        cross-tenant/app access when an end-user ID is known.
        """

        end_user = EndUserService.get_end_user_by_id(
            tenant_id=app_model.tenant_id, app_id=app_model.id, end_user_id=str(end_user_id)
        )
        if end_user is None:
            raise EndUserNotFoundError()

        return EndUserDetail.model_validate(end_user).model_dump(mode="json")


@service_api_ns.route("/end-users")
class EndUserSummaryApi(Resource):
    """Resource for retrieving an end user's data summary by external user ID."""

    @service_api_ns.doc(
        summary="Get End User Data Summary",
        description=(
            "Retrieve the data summary (conversation, message and uploaded file counts) for an end "
            "user of the current app, identified by the external `user` ID. Read-only: does not "
            "create or delete any data."
        ),
        tags=["End Users"],
        responses={
            200: "End user data summary retrieved successfully.",
            400: "`bad_request` : Query parameter `user` is required.",
            404: "`end_user_not_found` : End user not found.",
        },
    )
    @service_api_ns.doc("get_end_user_data_summary")
    @service_api_ns.doc(description="Get an end user's data summary by external user ID")
    @service_api_ns.doc(
        params={"user": "External user ID identifying the end user within the current app."},
        responses={
            200: "End user data summary retrieved successfully",
            400: "Bad request - query parameter `user` is required",
            401: "Unauthorized - invalid API token",
            404: "End user not found",
        },
    )
    @service_api_ns.response(
        200, "End user data summary retrieved successfully", service_api_ns.models[EndUserDataSummaryResponse.__name__]
    )
    @validate_app_token
    def get(self, app_model: App):
        """Get an end user's data summary.

        Read-only endpoint. Unlike other service API calls it does not use
        `fetch_user_arg`, so a missing end user is not auto-created and a
        non-resolvable ID returns 404 instead.
        """

        external_user_id = request.args.get("user")
        if not external_user_id:
            raise BadRequest("Query parameter 'user' is required.")

        end_user = EndUserService.get_end_user_by_external_id(
            tenant_id=app_model.tenant_id, app_id=app_model.id, external_user_id=external_user_id
        )
        if end_user is None:
            raise EndUserNotFoundError()

        summary = EndUserService.get_data_summary(app_model, end_user)
        response = EndUserDataSummaryResponse(
            end_user_id=end_user.id,
            conversation_count=summary.conversation_count,
            message_count=summary.message_count,
            upload_file_count=summary.upload_file_count,
        )
        return response.model_dump(mode="json")

    @service_api_ns.doc(
        summary="Delete End User Data",
        description=(
            "Delete the conversations and uploaded files of an end user of the current app, "
            "identified by the external `user` ID. The end user identity is preserved; only its "
            "owned data is removed (conversations immediately, files asynchronously). "
            "Idempotent: repeat calls return 202."
        ),
        tags=["End Users"],
        responses={
            202: "End user data deletion accepted.",
            400: (
                "`bad_request` : Query parameter `user` is required, or `user` resolves to the "
                "anonymous/DEFAULT-USER sentinel which cannot be deleted."
            ),
            404: "`end_user_not_found` : End user not found.",
        },
    )
    @service_api_ns.doc("delete_end_user_data")
    @service_api_ns.doc(description="Delete an end user's data by external user ID")
    @service_api_ns.doc(
        params={"user": "External user ID identifying the end user within the current app."},
        responses={
            202: "End user data deletion accepted",
            400: "Bad request - `user` is required or resolves to the anonymous sentinel",
            401: "Unauthorized - invalid API token",
            404: "End user not found",
        },
    )
    @service_api_ns.response(
        202, "End user data deletion accepted", service_api_ns.models[EndUserDeletionResponse.__name__]
    )
    @validate_app_token
    def delete(self, app_model: App):
        """Delete an end user's data.

        Guard: the anonymous/`DEFAULT-USER` sentinel is shared by every anonymous
        visitor of an app and must never be wiped. Returns 400 for it.
        """

        external_user_id = request.args.get("user")
        if not external_user_id:
            raise BadRequest("Query parameter 'user' is required.")

        end_user = EndUserService.get_end_user_by_external_id(
            tenant_id=app_model.tenant_id, app_id=app_model.id, external_user_id=external_user_id
        )
        if end_user is None:
            raise EndUserNotFoundError()

        if end_user.session_id == DefaultEndUserSessionID.DEFAULT_SESSION_ID:
            raise BadRequest("Cannot delete data for the anonymous user.")

        result = EndUserService.delete_all_data(app_model, end_user)
        response = EndUserDeletionResponse(
            end_user_id=end_user.id,
            conversations_marked=result.conversations_marked,
        )
        return response.model_dump(mode="json"), 202

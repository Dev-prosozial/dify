from flask_restx import Resource
from sqlalchemy.orm import sessionmaker

from controllers.common.schema import register_response_schema_models
from controllers.service_api import service_api_ns
from controllers.service_api.wraps import FetchUserArg, WhereisUserArg, validate_app_token
from extensions.ext_application_services import application_services
from extensions.ext_database import db
from fields.base import ResponseModel
from libs.external_api import error_responses
from models.enums import CreatorUserRole
from models.model import App, EndUser
from services.conversation_service import ConversationService


class UserDataCounts(ResponseModel):
    conversation_count: int
    upload_file_count: int


class UserDataDeletionSummary(ResponseModel):
    deleted_conversations: int
    deleted_upload_files: int
    failed_conversation_ids: list[str] = []
    failed_upload_file_ids: list[str] = []


register_response_schema_models(service_api_ns, UserDataCounts, UserDataDeletionSummary)


@service_api_ns.route("/user-data")
class UserDataApi(Resource):
    @error_responses()
    @service_api_ns.doc(
        summary="Get User Data Counts",
        description=(
            "Return the number of conversations and upload_files accumulated by a single end-user "
            "of the app. Only aggregate counts are returned; no lists or pagination."
        ),
        tags=["User Data"],
        responses={
            200: "Successfully retrieved user data counts.",
            401: "Unauthorized - invalid API token or missing `user`.",
        },
    )
    @service_api_ns.doc("get_user_data_counts")
    @service_api_ns.doc(description="Return aggregate counts of conversations and upload files for a user")
    @service_api_ns.response(
        200,
        "User data counts retrieved successfully",
        service_api_ns.models[UserDataCounts.__name__],
    )
    @validate_app_token(fetch_user_arg=FetchUserArg(fetch_from=WhereisUserArg.QUERY, required=True))
    def get(self, app_model: App, end_user: EndUser):
        """Return the count of conversations and upload_files for the current end-user."""
        with sessionmaker(db.engine).begin() as session:
            conversation_count = ConversationService.count_for_user(
                app_model,
                end_user,
                session=session,
            )
        upload_file_count = application_services().files.count_upload_files_by_user(
            tenant_id=app_model.tenant_id,
            user_id=end_user.id,
            role=CreatorUserRole.END_USER,
        )
        return UserDataCounts(
            conversation_count=conversation_count,
            upload_file_count=upload_file_count,
        ).model_dump(mode="json")

    @error_responses()
    @service_api_ns.doc(
        summary="Delete User Data",
        description=(
            "Delete all conversations and upload_files accumulated by a single end-user of the "
            "app. Conversations are soft-deleted (cleaned up asynchronously); upload_files are "
            "hard-deleted. Returns the numbers deleted and any upload_file ids that could not be "
            "removed."
        ),
        tags=["User Data"],
        responses={
            200: "Successfully deleted user data.",
            401: "Unauthorized - invalid API token or missing `user`.",
        },
    )
    @service_api_ns.doc("delete_user_data")
    @service_api_ns.doc(description="Delete all conversations and upload files for a user")
    @service_api_ns.response(
        200,
        "User data deleted successfully",
        service_api_ns.models[UserDataDeletionSummary.__name__],
    )
    @validate_app_token(fetch_user_arg=FetchUserArg(fetch_from=WhereisUserArg.QUERY, required=True))
    def delete(self, app_model: App, end_user: EndUser):
        """Delete all conversations and upload_files for the current end-user."""
        deleted_conversations, failed_conversation_ids = ConversationService.delete_all_for_user(
            app_model,
            end_user,
            session=db.session(),
        )
        deleted_upload_files, failed_upload_file_ids = application_services().files.delete_files_by_user(
            tenant_id=app_model.tenant_id,
            user_id=end_user.id,
            role=CreatorUserRole.END_USER,
        )
        return UserDataDeletionSummary(
            deleted_conversations=deleted_conversations,
            deleted_upload_files=deleted_upload_files,
            failed_conversation_ids=failed_conversation_ids,
            failed_upload_file_ids=failed_upload_file_ids,
        ).model_dump(mode="json")

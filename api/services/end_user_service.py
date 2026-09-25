import logging
from collections.abc import Mapping
from dataclasses import dataclass
from enum import StrEnum

from sqlalchemy import case, func, select, update
from sqlalchemy.orm import sessionmaker

from extensions.ext_database import db
from models.enums import CreatorUserRole, EndUserType
from models.model import App, Conversation, DefaultEndUserSessionID, EndUser, Message, UploadFile
from tasks.delete_conversation_task import delete_conversation_related_data
from tasks.delete_end_user_data_task import delete_end_user_upload_files

logger = logging.getLogger(__name__)

# Conversations at or below this count are dispatched for physical cleanup
# per-conversation. Above it we rely on the periodic sweeper to avoid
# flooding the broker with tasks for very large histories.
_HYBRID_ENQUEUE_THRESHOLD = 200


class EndUserDataType(StrEnum):
    ALL = "all"
    UPLOAD_FILES = "upload_files"
    CONVERSATIONS = "conversations"


@dataclass
class EndUserDataSummary:
    conversation_count: int
    message_count: int
    upload_file_count: int


@dataclass
class EndUserDeletionResult:
    conversations_marked: int


class EndUserService:
    """
    Service for managing end users.
    """

    @classmethod
    def get_end_user_by_id(cls, *, tenant_id: str, app_id: str, end_user_id: str) -> EndUser | None:
        """Get an end user by primary key.

        This is scoped to the provided tenant and app to prevent cross-tenant/app access
        when an end-user ID is known.
        """

        with sessionmaker(bind=db.engine, expire_on_commit=False).begin() as session:
            return session.scalar(
                select(EndUser)
                .where(
                    EndUser.id == end_user_id,
                    EndUser.tenant_id == tenant_id,
                    EndUser.app_id == app_id,
                )
                .limit(1)
            )

    @classmethod
    def get_end_user_by_external_id(cls, *, tenant_id: str, app_id: str, external_user_id: str) -> EndUser | None:
        """Get an end user by external user ID.

        Matches the identity a backend passes to other service API calls as
        `user`. The column is not indexed and is kept equal to `session_id`,
        so we query by `session_id` which is indexed and scoped to the provided
        tenant and app to prevent cross-tenant/app access.
        """

        with sessionmaker(bind=db.engine, expire_on_commit=False).begin() as session:
            return session.scalar(
                select(EndUser)
                .where(
                    EndUser.tenant_id == tenant_id,
                    EndUser.app_id == app_id,
                    EndUser.session_id == external_user_id,
                    EndUser.type != EndUserType.APP_DEPLOY,
                )
                .limit(1)
            )

    @classmethod
    def get_or_create_end_user(cls, app_model: App, user_id: str | None = None) -> EndUser:
        """
        Get or create an end user for a given app.
        """

        return cls.get_or_create_end_user_by_type(EndUserType.SERVICE_API, app_model.tenant_id, app_model.id, user_id)

    @classmethod
    def get_or_create_end_user_by_type(
        cls, type: EndUserType, tenant_id: str, app_id: str, user_id: str | None = None
    ) -> EndUser:
        """
        Get or create an end user for a given app and type.
        """

        if not user_id:
            user_id = DefaultEndUserSessionID.DEFAULT_SESSION_ID

        with sessionmaker(bind=db.engine, expire_on_commit=False).begin() as session:
            # Query with ORDER BY to prioritize exact type matches while maintaining backward compatibility
            # This single query approach is more efficient than separate queries
            end_user = session.scalar(
                select(EndUser)
                .where(
                    EndUser.tenant_id == tenant_id,
                    EndUser.app_id == app_id,
                    EndUser.session_id == user_id,
                    # An AppDeploy row is never a legacy row this could upgrade:
                    # the type was added after the split, and FileGrantService
                    # reads its rows by type. Retyping one here would hide it
                    # from that read and strand the files it owns.
                    EndUser.type != EndUserType.APP_DEPLOY,
                )
                .order_by(
                    # Prioritize records with matching type (0 = match, 1 = no match)
                    case((EndUser.type == type, 0), else_=1)
                )
                .limit(1)
            )

            if end_user:
                # If found a legacy end user with different type, update it for future consistency
                if end_user.type != type:
                    logger.info(
                        "Upgrading legacy EndUser %s from type=%s to %s for session_id=%s",
                        end_user.id,
                        end_user.type,
                        type,
                        user_id,
                    )
                    end_user.type = type
            else:
                # Create new end user if none exists
                end_user = EndUser(
                    tenant_id=tenant_id,
                    app_id=app_id,
                    type=type,
                    is_anonymous=user_id == DefaultEndUserSessionID.DEFAULT_SESSION_ID,
                    session_id=user_id,
                    external_user_id=user_id,
                )
                session.add(end_user)

        return end_user

    @classmethod
    def create_end_user_batch(
        cls, type: EndUserType, tenant_id: str, app_ids: list[str], user_id: str
    ) -> Mapping[str, EndUser]:
        """Create end users in batch.

        Creates end users in batch for the specified tenant and application IDs in O(1) time.

        This batch creation is necessary because trigger subscriptions can span multiple applications,
        and trigger events may be dispatched to multiple applications simultaneously.

        For each app_id in app_ids, check if an `EndUser` with the given
        `user_id` (as session_id/external_user_id) already exists for the
        tenant/app and type `type`. If it exists, return it; otherwise,
        create it. Operates with minimal DB I/O by querying and inserting in
        batches.

        Returns a mapping of `app_id -> EndUser`.
        """

        # Normalize user_id to default if empty
        if not user_id:
            user_id = DefaultEndUserSessionID.DEFAULT_SESSION_ID

        # Deduplicate app_ids while preserving input order
        seen: set[str] = set()
        unique_app_ids: list[str] = []
        for app_id in app_ids:
            if app_id not in seen:
                seen.add(app_id)
                unique_app_ids.append(app_id)

        # Result is a simple app_id -> EndUser mapping
        result: dict[str, EndUser] = {}
        if not unique_app_ids:
            return result

        with sessionmaker(bind=db.engine, expire_on_commit=False).begin() as session:
            # Fetch existing end users for all target apps in a single query
            existing_end_users: list[EndUser] = list(
                session.scalars(
                    select(EndUser).where(
                        EndUser.tenant_id == tenant_id,
                        EndUser.app_id.in_(unique_app_ids),
                        EndUser.session_id == user_id,
                        EndUser.type == type,
                    )
                ).all()
            )

            found_app_ids: set[str] = set()
            for eu in existing_end_users:
                # If duplicates exist due to weak DB constraints, prefer the first
                if eu.app_id not in result:
                    result[eu.app_id] = eu
                found_app_ids.add(eu.app_id)

            # Determine which apps still need an EndUser created
            missing_app_ids = [app_id for app_id in unique_app_ids if app_id not in found_app_ids]

            if missing_app_ids:
                new_end_users: list[EndUser] = []
                is_anonymous = user_id == DefaultEndUserSessionID.DEFAULT_SESSION_ID
                for app_id in missing_app_ids:
                    new_end_users.append(
                        EndUser(
                            tenant_id=tenant_id,
                            app_id=app_id,
                            type=type,
                            is_anonymous=is_anonymous,
                            session_id=user_id,
                            external_user_id=user_id,
                        )
                    )

                session.add_all(new_end_users)

                for eu in new_end_users:
                    result[eu.app_id] = eu

        return result

    @classmethod
    def get_data_summary(cls, app_model: App, end_user: EndUser) -> EndUserDataSummary:
        """Count the data owned by a single end user in a given app.

        The conversation and message counts are scoped by `app_id` and
        `from_end_user_id` and are served by existing indexes. The upload file
        count is scoped by `created_by`/`created_by_role`; `created_by` is not
        indexed, so it performs a sequential scan (an accepted v1 limitation).
        """

        with sessionmaker(bind=db.engine, expire_on_commit=False).begin() as session:
            conversation_count = session.scalar(
                select(func.count())
                .select_from(Conversation)
                .where(
                    Conversation.app_id == app_model.id,
                    Conversation.from_end_user_id == end_user.id,
                    Conversation.is_deleted.is_(False),
                )
            )
            message_count = session.scalar(
                select(func.count())
                .select_from(Message)
                .where(
                    Message.app_id == app_model.id,
                    Message.from_end_user_id == end_user.id,
                )
            )
            upload_file_count = session.scalar(
                select(func.count())
                .select_from(UploadFile)
                .where(
                    UploadFile.created_by == end_user.id,
                    UploadFile.created_by_role == CreatorUserRole.END_USER,
                )
            )

        return EndUserDataSummary(
            conversation_count=conversation_count or 0,
            message_count=message_count or 0,
            upload_file_count=upload_file_count or 0,
        )

    @classmethod
    def delete_all_data(cls, app_model: App, end_user: EndUser, data_type: EndUserDataType) -> EndUserDeletionResult:
        """Delete a subset of an end user's data and enqueue physical cleanup.

        The ``EndUser`` row itself is preserved (only its owned data is removed).
        ``data_type`` selects which data is deleted: conversations only, upload
        files only, or both. Conversations are soft-deleted (``is_deleted=True``)
        immediately; physical cleanup runs asynchronously via Celery. The caller
        is responsible for guarding against the shared anonymous/`DEFAULT-USER`
        sentinel before invoking this method.
        """

        conversation_ids: list[str] = []
        if data_type in (EndUserDataType.ALL, EndUserDataType.CONVERSATIONS):
            with sessionmaker(bind=db.engine, expire_on_commit=False).begin() as session:
                result = session.execute(
                    update(Conversation)
                    .where(
                        Conversation.app_id == app_model.id,
                        Conversation.from_end_user_id == end_user.id,
                        Conversation.is_deleted.is_(False),
                    )
                    .values(is_deleted=True)
                    .returning(Conversation.id)
                )
                conversation_ids = [row[0] for row in result]

            cls._dispatch_conversation_cleanup(conversation_ids)

        if data_type in (EndUserDataType.ALL, EndUserDataType.UPLOAD_FILES):
            try:
                delete_end_user_upload_files.delay(app_model.tenant_id, end_user.id)
            except Exception:
                logger.exception("Failed to enqueue upload file cleanup for end user %s", end_user.id)

        return EndUserDeletionResult(conversations_marked=len(conversation_ids))

    @classmethod
    def _dispatch_conversation_cleanup(cls, conversation_ids: list[str]) -> None:
        """Dispatch physical cleanup for a bounded set of soft-deleted conversations."""
        if len(conversation_ids) > _HYBRID_ENQUEUE_THRESHOLD:
            # The periodic sweeper re-enqueues soft-deleted conversations, so a
            # broker outage or very large history must not resurrect them.
            logger.info(
                "Skipping per-conversation dispatch for %s conversations; relying on periodic sweeper.",
                len(conversation_ids),
            )
            return

        for conversation_id in conversation_ids:
            try:
                delete_conversation_related_data.delay(conversation_id)
            except Exception:
                # The soft-deleted row is a durable cleanup marker picked up by
                # the periodic sweeper, so a broker outage is non-fatal here.
                logger.exception("Failed to enqueue cleanup for conversation %s", conversation_id)

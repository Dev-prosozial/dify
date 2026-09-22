import logging
import time

import click
from celery import shared_task
from sqlalchemy import delete, select

from core.db.session_factory import session_factory
from extensions.ext_storage import storage
from libs.batch_sql_delete import batch_delete_by_query
from models import UploadFile
from models.enums import CreatorUserRole

logger = logging.getLogger(__name__)

UPLOAD_FILES_QUERY = """
select id from upload_files
where tenant_id = :tenant_id
  and created_by = :created_by
  and created_by_role = :created_by_role
limit 1000
"""


def _delete_storage_object(file_key: str) -> None:
    try:
        storage.delete(file_key)
    except Exception:
        # A prior attempt may have deleted the object before its DB transaction
        # rolled back. Only suppress the retry when the backend confirms absence.
        if storage.exists(file_key):
            raise
        logger.info("Storage object %s was already absent", file_key)


def _delete_upload_file(session, upload_file_id: str) -> None:
    upload_file = session.scalar(select(UploadFile).where(UploadFile.id == upload_file_id).limit(1))
    if upload_file is None:
        return
    _delete_storage_object(upload_file.key)
    session.execute(
        delete(UploadFile).where(UploadFile.id == upload_file_id).execution_options(synchronize_session=False)
    )


def _delete_end_user_upload_files(tenant_id: str, end_user_id: str) -> None:
    batch_delete_by_query(
        UPLOAD_FILES_QUERY,
        {"tenant_id": tenant_id, "created_by": end_user_id, "created_by_role": CreatorUserRole.END_USER.value},
        _delete_upload_file,
        "upload file",
        session_factory=session_factory.create_session,
    )


@shared_task(queue="conversation", bind=True, max_retries=5, default_retry_delay=30)
def delete_end_user_upload_files(self, tenant_id: str, end_user_id: str) -> None:
    """Physically remove upload files owned by a single end user.

    The storage object is deleted before the ``UploadFile`` row so a failed
    attempt retains the durable ``key`` needed by the next retry. Batching is
    delegated to :func:`batch_delete_by_query` with commit-per-record semantics.
    """

    logger.info(
        click.style(f"Starting to delete upload files for end user {end_user_id} in tenant {tenant_id}", fg="green")
    )
    start_at = time.perf_counter()

    try:
        _delete_end_user_upload_files(tenant_id, end_user_id)
    except Exception as exc:
        logger.exception("Failed to delete upload files for end user: %s", end_user_id)
        countdown = min(30 * (2**self.request.retries), 10 * 60)
        raise self.retry(exc=exc, countdown=countdown)

    end_at = time.perf_counter()
    logger.info(
        click.style(
            f"Finished deleting upload files for end user {end_user_id}, latency: {end_at - start_at}",
            fg="green",
        )
    )

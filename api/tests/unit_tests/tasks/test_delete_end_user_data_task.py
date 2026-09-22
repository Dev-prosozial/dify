from datetime import UTC, datetime
from unittest.mock import patch
from uuid import uuid4

from sqlalchemy.orm import Session

from extensions.storage.storage_type import StorageType
from models import UploadFile
from models.enums import CreatorUserRole
from tasks.delete_end_user_data_task import _delete_end_user_upload_files

TENANT_ID = "11111111-1111-1111-1111-111111111111"
END_USER_ID = "22222222-2222-2222-2222-222222222222"
OTHER_END_USER_ID = "33333333-3333-3333-3333-333333333333"


def _upload_file(name: str, *, created_by: str) -> UploadFile:
    return UploadFile(
        tenant_id=TENANT_ID,
        storage_type=StorageType.S3,
        key=f"upload_files/{TENANT_ID}/{name}",
        name=name,
        size=5,
        extension="txt",
        mime_type="text/plain",
        created_by_role=CreatorUserRole.END_USER,
        created_by=created_by,
        created_at=datetime.now(UTC),
        used=False,
    )


def test_delete_end_user_upload_files_removes_owned_rows_and_storage(sqlite_session: Session) -> None:
    owned = _upload_file("owned.txt", created_by=END_USER_ID)
    other_user_file = _upload_file("other-user.txt", created_by=OTHER_END_USER_ID)
    sqlite_session.add_all([owned, other_user_file])
    sqlite_session.commit()
    owned_key = owned.key
    owned_id = owned.id
    other_file_id = other_user_file.id

    with patch("tasks.delete_end_user_data_task.storage") as storage_mock:
        _delete_end_user_upload_files(TENANT_ID, END_USER_ID)

    storage_mock.delete.assert_called_once_with(owned_key)
    sqlite_session.expire_all()
    assert sqlite_session.get(UploadFile, owned_id) is None
    assert sqlite_session.get(UploadFile, other_file_id) is not None


def test_delete_end_user_upload_files_only_touches_target_tenant(sqlite_session: Session) -> None:
    owned = _upload_file("owned.txt", created_by=END_USER_ID)
    owned_key = owned.key
    other_tenant_file = UploadFile(
        tenant_id="99999999-9999-9999-9999-999999999999",
        storage_type=StorageType.S3,
        key="upload_files/other-tenant/owned.txt",
        name="owned.txt",
        size=5,
        extension="txt",
        mime_type="text/plain",
        created_by_role=CreatorUserRole.END_USER,
        created_by=END_USER_ID,
        created_at=datetime.now(UTC),
        used=False,
    )
    sqlite_session.add_all([owned, other_tenant_file])
    sqlite_session.commit()
    owned_id = owned.id
    other_tenant_id = other_tenant_file.id

    with patch("tasks.delete_end_user_data_task.storage") as storage_mock:
        _delete_end_user_upload_files(TENANT_ID, END_USER_ID)

    sqlite_session.expunge_all()
    assert sqlite_session.get(UploadFile, owned_id) is None
    assert sqlite_session.get(UploadFile, other_tenant_id) is not None
    storage_mock.delete.assert_called_once_with(owned_key)


def test_delete_end_user_upload_files_none_for_unknown_user(sqlite_session: Session) -> None:
    existing = _upload_file("kept.txt", created_by=OTHER_END_USER_ID)
    sqlite_session.add(existing)
    sqlite_session.commit()

    with patch("tasks.delete_end_user_data_task.storage") as storage_mock:
        _delete_end_user_upload_files(TENANT_ID, str(uuid4()))

    storage_mock.delete.assert_not_called()
    sqlite_session.expire_all()
    assert sqlite_session.get(UploadFile, existing.id) is not None

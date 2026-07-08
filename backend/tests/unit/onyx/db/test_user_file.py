"""Unit tests for get_file_id_by_user_file_id() in onyx.db.user_file.

FORK: miro - regression test for the /chat/file 500: UserFile.id is
UUID-typed, but the connector-file ACL path (fetch_chat_file) passes
non-UserFile ids like "miro__<board>__<item>" through this lookup first.
"""

from unittest.mock import MagicMock

from onyx.db.user_file import get_file_id_by_user_file_id


class TestGetFileIdByUserFileId:
    def test_non_uuid_input_returns_none_without_raising(
        self, mock_db_session: MagicMock
    ) -> None:
        result = get_file_id_by_user_file_id(
            "miro__uXjVH_7LB9o=__abc123", mock_db_session
        )

        assert result is None
        # Should short-circuit before ever querying the UUID-typed column.
        mock_db_session.query.assert_not_called()

    def test_empty_string_returns_none_without_raising(
        self, mock_db_session: MagicMock
    ) -> None:
        result = get_file_id_by_user_file_id("", mock_db_session)

        assert result is None
        mock_db_session.query.assert_not_called()

    def test_valid_uuid_with_matching_user_file_returns_file_id(
        self, mock_db_session: MagicMock
    ) -> None:
        user_file = MagicMock()
        user_file.file_id = "file-store-id-1"
        mock_db_session.query.return_value.filter.return_value.first.return_value = (
            user_file
        )

        result = get_file_id_by_user_file_id(
            "5b1b2c3d-4e5f-6789-abcd-ef0123456789", mock_db_session
        )

        assert result == "file-store-id-1"

    def test_valid_uuid_with_no_matching_user_file_returns_none(
        self, mock_db_session: MagicMock
    ) -> None:
        mock_db_session.query.return_value.filter.return_value.first.return_value = (
            None
        )

        result = get_file_id_by_user_file_id(
            "5b1b2c3d-4e5f-6789-abcd-ef0123456789", mock_db_session
        )

        assert result is None

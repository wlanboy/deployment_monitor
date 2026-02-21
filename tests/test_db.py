from unittest.mock import MagicMock, patch

import db


class TestInitDb:
    def test_creates_deployments_table(self):
        mock_conn = MagicMock()
        mock_cursor = MagicMock()
        mock_conn.cursor.return_value = mock_cursor

        with patch("db.sqlite3.connect", return_value=mock_conn):
            db.init_db()

        sql = mock_cursor.execute.call_args[0][0]
        assert "CREATE TABLE IF NOT EXISTS deployments" in sql

    def test_commits_and_closes(self):
        mock_conn = MagicMock()
        mock_conn.cursor.return_value = MagicMock()

        with patch("db.sqlite3.connect", return_value=mock_conn):
            db.init_db()

        mock_conn.commit.assert_called_once()
        mock_conn.close.assert_called_once()

    def test_connects_to_correct_db(self):
        mock_conn = MagicMock()
        mock_conn.cursor.return_value = MagicMock()

        with patch("db.sqlite3.connect", return_value=mock_conn) as mock_connect:
            db.init_db()

        mock_connect.assert_called_once_with("deployment.db")


class TestLogDeployment:
    def test_inserts_correct_values(self):
        mock_conn = MagicMock()
        mock_cursor = MagicMock()
        mock_conn.cursor.return_value = mock_cursor

        with patch("db.sqlite3.connect", return_value=mock_conn):
            db.log_deployment("site.yml", "2024-01-01T10:00:00", "2024-01-01T10:01:00", 60, 0, 1)

        sql, params = mock_cursor.execute.call_args[0]
        assert "INSERT INTO deployments" in sql
        assert params == ("site.yml", "2024-01-01T10:00:00", "2024-01-01T10:01:00", 60, 0, 1)

    def test_commits_and_closes(self):
        mock_conn = MagicMock()
        mock_conn.cursor.return_value = MagicMock()

        with patch("db.sqlite3.connect", return_value=mock_conn):
            db.log_deployment("pb.yml", "s", "e", 10, 1, 2)

        mock_conn.commit.assert_called_once()
        mock_conn.close.assert_called_once()

    def test_non_zero_status_stored(self):
        mock_conn = MagicMock()
        mock_cursor = MagicMock()
        mock_conn.cursor.return_value = mock_cursor

        with patch("db.sqlite3.connect", return_value=mock_conn):
            db.log_deployment("fail.yml", "s", "e", 5, 2, 3)

        _, params = mock_cursor.execute.call_args[0]
        assert params[4] == 2  # status
        assert params[5] == 3  # retries

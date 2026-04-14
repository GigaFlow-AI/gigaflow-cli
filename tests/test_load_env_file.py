"""Unit tests for gigaflow._setup.load_env_file."""

from pathlib import Path

from gigaflow._setup import load_env_file


def write(tmp_path: Path, content: str) -> str:
    p = tmp_path / "test.env"
    p.write_text(content)
    return str(p)


class TestLoadEnvFile:
    def test_basic_key_value(self, tmp_path):
        path = write(tmp_path, "FOO=bar\nBAZ=qux\n")
        assert load_env_file(path) == {"FOO": "bar", "BAZ": "qux"}

    def test_comments_skipped(self, tmp_path):
        path = write(tmp_path, "# this is a comment\nKEY=value\n# another comment\n")
        assert load_env_file(path) == {"KEY": "value"}

    def test_blank_lines_skipped(self, tmp_path):
        path = write(tmp_path, "\nKEY=value\n\n")
        assert load_env_file(path) == {"KEY": "value"}

    def test_double_quoted_value(self, tmp_path):
        path = write(tmp_path, 'KEY="hello world"\n')
        assert load_env_file(path) == {"KEY": "hello world"}

    def test_single_quoted_value(self, tmp_path):
        path = write(tmp_path, "KEY='hello world'\n")
        assert load_env_file(path) == {"KEY": "hello world"}

    def test_unquoted_value_with_equals(self, tmp_path):
        """Values that contain = (e.g. URLs, base64) are preserved correctly."""
        path = write(tmp_path, "DB_URL=postgresql://user:pass@host/db\n")
        assert load_env_file(path) == {"DB_URL": "postgresql://user:pass@host/db"}

    def test_empty_value(self, tmp_path):
        path = write(tmp_path, "KEY=\n")
        assert load_env_file(path) == {"KEY": ""}

    def test_whitespace_around_key_and_value(self, tmp_path):
        path = write(tmp_path, "  KEY  =  value  \n")
        assert load_env_file(path) == {"KEY": "value"}

    def test_line_without_equals_skipped(self, tmp_path):
        path = write(tmp_path, "NOEQUALS\nKEY=value\n")
        assert load_env_file(path) == {"KEY": "value"}

    def test_all_gigaflow_keys(self, tmp_path):
        content = (
            "OPENAI_API_KEY=sk-test\n"
            "GIGAFLOW_TRANSFORM_YML=/path/to/transform.yml\n"
            "GIGAFLOW_PROJECT_NAME=my-project\n"
            "GIGAFLOW_DB_HOST=localhost\n"
            "GIGAFLOW_DB_PORT=5432\n"
            "GIGAFLOW_DB_USER=postgres\n"
            "GIGAFLOW_DB_PASSWORD=secret\n"
            "GIGAFLOW_DB_NAME=mydb\n"
            "GIGAFLOW_DB_TABLE=spans\n"
        )
        path = write(tmp_path, content)
        result = load_env_file(path)
        assert result["OPENAI_API_KEY"] == "sk-test"
        assert result["GIGAFLOW_DB_PORT"] == "5432"
        assert result["GIGAFLOW_DB_PASSWORD"] == "secret"
        assert result["GIGAFLOW_TRANSFORM_YML"] == "/path/to/transform.yml"

    def test_missing_file_returns_empty_dict(self, tmp_path):
        result = load_env_file(str(tmp_path / "nonexistent.env"))
        assert result == {}

    def test_mixed_content(self, tmp_path):
        content = (
            "# GigaFlow env\n"
            "\n"
            "OPENAI_API_KEY=sk-abc\n"
            "# leave blank:\n"
            "GIGAFLOW_TRANSFORM_YML=\n"
            "GIGAFLOW_DB_PORT=5433\n"
        )
        path = write(tmp_path, content)
        result = load_env_file(path)
        assert result == {
            "OPENAI_API_KEY": "sk-abc",
            "GIGAFLOW_TRANSFORM_YML": "",
            "GIGAFLOW_DB_PORT": "5433",
        }

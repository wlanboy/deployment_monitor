import os

import pytest

from validation import (
    BranchNameError,
    PathNotAllowedError,
    redact,
    resolve_allowed_path,
    validate_branch,
)


@pytest.fixture
def root(tmp_path):
    base = tmp_path / "base"
    (base / "playbooks").mkdir(parents=True)
    (base / "playbooks" / "site.yaml").write_text("---\n")
    (tmp_path / "secret.yaml").write_text("geheim\n")
    return base


class TestResolveAllowedPath:
    def test_allowed_path_resolved(self, root):
        assert resolve_allowed_path("playbooks/site.yaml", ["playbooks/site.yaml"], str(root)) == \
            os.path.realpath(root / "playbooks" / "site.yaml")

    def test_not_whitelisted(self, root):
        with pytest.raises(PathNotAllowedError):
            resolve_allowed_path("playbooks/site.yaml", [], str(root))

    @pytest.mark.parametrize("relative", ["../secret.yaml", "playbooks/../../secret.yaml"])
    def test_traversal_rejected_even_if_whitelisted(self, root, relative):
        with pytest.raises(PathNotAllowedError):
            resolve_allowed_path(relative, [relative], str(root))

    def test_absolute_path_rejected_even_if_whitelisted(self, root, tmp_path):
        target = str(tmp_path / "secret.yaml")
        with pytest.raises(PathNotAllowedError):
            resolve_allowed_path(target, [target], str(root))

    def test_symlink_escape_rejected(self, root, tmp_path):
        (root / "playbooks" / "link.yaml").symlink_to(tmp_path / "secret.yaml")
        with pytest.raises(PathNotAllowedError):
            resolve_allowed_path("playbooks/link.yaml", ["playbooks/link.yaml"], str(root))

    def test_missing_file(self, root):
        with pytest.raises(FileNotFoundError):
            resolve_allowed_path("playbooks/missing.yaml", ["playbooks/missing.yaml"], str(root))

    def test_directory_is_not_a_file(self, root):
        with pytest.raises(FileNotFoundError):
            resolve_allowed_path("playbooks", ["playbooks"], str(root))


class TestValidateBranch:
    @pytest.mark.parametrize("branch", ["main", "feature/x", "release-1.0", "fix_123", "a/b/c"])
    def test_valid(self, branch):
        validate_branch(branch)

    @pytest.mark.parametrize("branch", [
        "", "-f", "--force", "--orphan=x", "a..b", "@{-1}", "HEAD@{1}", "foo.lock", "x/", "x.",
        ".hidden", "a/.b", "a//b", "with space", "x;id", "$(id)", "a~1", "a^", "a:b", "b" * 201,
    ])
    def test_invalid(self, branch):
        with pytest.raises(BranchNameError):
            validate_branch(branch)


class TestRedact:
    def test_url_credentials(self):
        assert redact("https://user:s3cret@git.example.com/repo.git") == "https://***@git.example.com/repo.git"

    def test_url_token_without_user(self):
        assert redact("https://ghp_abc123@github.com/x") == "https://***@github.com/x"

    @pytest.mark.parametrize("text", ["password=s3cret", "token: s3cret", '"api_key": "s3cret"', "SECRET='s3cret'"])
    def test_secret_assignments(self, text):
        assert "s3cret" not in redact(text)

    def test_plain_text_unchanged(self):
        line = "TASK [Create folder] ok: [localhost]"
        assert redact(line) == line

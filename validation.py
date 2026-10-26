import os
import re
from collections.abc import Iterable

# Branch-Namen: muss mit Buchstabe/Ziffer beginnen (kein "-" → keine Option für git)
_BRANCH_PATTERN = re.compile(r"[A-Za-z0-9][A-Za-z0-9._/-]{0,199}")

# Zugangsdaten in URLs (https://user:token@host) und typische key=value-Secrets
_URL_CREDENTIALS = re.compile(r"\b([a-zA-Z][a-zA-Z0-9+.-]*://)[^/\s:@]+(?::[^/\s@]*)?@")
_SECRET_ASSIGNMENT = re.compile(
    r"(?i)\b(password|passwd|token|secret|api[_-]?key)([\"']?\s*[=:]\s*)(\"[^\"]*\"|'[^']*'|\S+)"
)


class PathNotAllowedError(ValueError):
    pass


class BranchNameError(ValueError):
    pass


def resolve_allowed_path(relative: str, allowed: Iterable[str], root: str) -> str:
    """Löst einen freigegebenen, relativen Pfad unterhalb von root auf.

    Wirft PathNotAllowedError, wenn der Pfad nicht in der Whitelist steht oder
    (z.B. über "..", absolute Pfade oder Symlinks) außerhalb von root landet,
    und FileNotFoundError, wenn die Datei nicht existiert.
    """
    if relative not in set(allowed):
        raise PathNotAllowedError(f"'{relative}' ist nicht freigegeben")
    root_real = os.path.realpath(root)
    full = os.path.realpath(os.path.join(root_real, relative))
    if os.path.commonpath([root_real, full]) != root_real:
        raise PathNotAllowedError(f"'{relative}' liegt außerhalb von base_path")
    if not os.path.isfile(full):
        raise FileNotFoundError(relative)
    return full


def validate_branch(branch: str) -> None:
    if (
        not _BRANCH_PATTERN.fullmatch(branch)
        or ".." in branch
        or "//" in branch
        or "/." in branch
        or branch.endswith((".", "/", ".lock"))
    ):
        raise BranchNameError(f"Ungültiger Branch-Name: {branch!r}")


def redact(text: str) -> str:
    text = _URL_CREDENTIALS.sub(r"\1***@", text)
    return _SECRET_ASSIGNMENT.sub(r"\1\2***", text)

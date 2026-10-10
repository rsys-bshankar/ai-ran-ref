"""Read a secret from the environment or from a file (PR-SEC-4.2).

A value in an environment variable is visible to anyone who can run `docker inspect` or read
`/proc/<pid>/environ`, and tends to end up in logs and crash dumps. The `*_FILE` convention (used by the
official Postgres image, among others) keeps the value in a file, which an orchestrator mounts from a secret
store (Docker/Compose secrets at `/run/secrets/<name>`, a Kubernetes Secret volume):

    read_secret("SMO_DATABASE_PASSWORD")
        -> the value of SMO_DATABASE_PASSWORD, or the contents of the file named by SMO_DATABASE_PASSWORD_FILE

  - neither set: None (the caller decides whether that is an error);
  - both set: `SecretConflict`, because which one wins is not something to guess about a credential;
  - the file missing or unreadable: `SecretFileError`, naming the variable and path, never the contents;
  - a single trailing newline is removed (editors and `echo` add one); nothing else is trimmed, so a secret
    that really ends in a space survives. An empty value counts as not set.
"""

import os
from collections.abc import Mapping


class SecretConflict(RuntimeError):
    """Both `NAME` and `NAME_FILE` are set."""


class SecretFileError(RuntimeError):
    """`NAME_FILE` names a file that cannot be read."""


def read_secret(name: str, environ: Mapping[str, str] = os.environ) -> str | None:
    """Returns the secret named `name` from `environ[name]` or from the file named by `environ[name + "_FILE"]`; None when neither is set or the value
    is empty.

    Raises SecretConflict when both are set and SecretFileError when the file cannot be read (the message names the variable and path, never the
    contents). One trailing newline is removed from a file's text.
    """
    value = environ.get(name) or ""
    path = environ.get(f"{name}_FILE") or ""
    if value and path:
        raise SecretConflict(f"both {name} and {name}_FILE are set: set only one")
    if path:
        try:
            with open(path, encoding="utf-8") as handle:
                text = handle.read()
        except OSError as exc:
            raise SecretFileError(f"{name}_FILE={path} cannot be read: {exc.strerror or type(exc).__name__}") from exc
        value = text[:-1] if text.endswith("\n") else text
    return value or None

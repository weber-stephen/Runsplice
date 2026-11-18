"""Shared, deliberately small contract for SDK session capture."""

import importlib
from pathlib import Path

from .privacy import environment_secrets
from .store import RunStore


def load_factory(path):
    module, separator, name = path.partition(":")
    if not separator or not module or not name or "." in name:
        raise ValueError("factory must be an importable module:function")
    try:
        factory = getattr(importlib.import_module(module), name)
    except (ImportError, AttributeError) as exc:
        raise ValueError(f"cannot import saved factory {path!r}; make its module and function importable in this environment") from exc
    if not callable(factory):
        raise ValueError("factory is not callable")
    return factory


def private_store(root, fields=()):
    return RunStore(Path(root), frozenset(fields), environment_secrets())

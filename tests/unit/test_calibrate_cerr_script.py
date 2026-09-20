"""scripts/calibrate_cerr.py -- the MT5 calls it makes must exist.

This file exists because the script cannot be executed in CI or on a
Linux machine: MetaTrader5 is a Windows package and the terminal must be
running. So the first real run is on the operator's desktop, and a
misspelled method there costs a round trip -- as `client.disconnect()`
did, when the method is `client.shutdown()`.

An AST scan is the cheap substitute for running it: every attribute the
script calls on the MT5 client is checked against the client's actual
API, and the module is imported to prove it at least loads without the
Windows package present."""

from __future__ import annotations

import ast
from pathlib import Path

_REPO = Path(__file__).resolve().parents[2]
_SCRIPT = _REPO / "scripts" / "calibrate_cerr.py"


def _client_attributes_called() -> set[str]:
    """Every `client.<name>(...)` in the script."""
    tree = ast.parse(_SCRIPT.read_text(encoding="utf-8"))
    names: set[str] = set()
    for node in ast.walk(tree):
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and isinstance(node.func.value, ast.Name)
            and node.func.value.id == "client"
        ):
            names.add(node.func.attr)
    return names


def test_the_script_only_calls_methods_the_client_actually_has() -> None:
    from vo.market.mt5 import MT5ReadClient

    called = _client_attributes_called()
    assert called, "no client calls found -- has the script been renamed?"

    for name in sorted(called):
        assert hasattr(MT5ReadClient, name), (
            f"scripts/calibrate_cerr.py calls client.{name}(), which "
            f"MT5ReadClient does not define"
        )


def test_the_teardown_is_the_one_the_client_defines() -> None:
    """Pinned by name: the read client shuts the terminal connection down
    with shutdown(), and an invented disconnect() fails only at runtime,
    inside a finally block, where it masks whatever else went wrong."""
    from vo.market.mt5 import MT5ReadClient

    assert hasattr(MT5ReadClient, "shutdown")
    assert not hasattr(MT5ReadClient, "disconnect")
    assert "shutdown" in _client_attributes_called()


def test_the_script_imports_without_the_windows_package() -> None:
    """MetaTrader5 is imported lazily inside vo.market.mt5, so everything
    up to an actual terminal call must work anywhere."""
    import importlib.util

    spec = importlib.util.spec_from_file_location("_calibrate_cerr", _SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    assert hasattr(module, "main")
    assert hasattr(module, "DISTRIBUTION_FIELDS")

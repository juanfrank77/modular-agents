"""
tests/conftest.py
------------------
Session-wide pytest fixtures for the modular-agents test suite.

Currently provides a tripwire for a subtle cross-test pollution pattern:
`core.agent_discovery` imports agent modules with a manual
`importlib.util.exec_module`, which (unlike the normal import machinery) does
not bind the submodule onto its parent package. When discovery runs in the
same process as other tests, later `mock.patch("agents.<name>.agent.x")` calls
fail with `AttributeError: module 'agents.<name>' has no attribute 'agent'`,
and the real error surfaces in unrelated tests.

The autouse session fixture below asserts that every `agents.<name>.agent`
module ends the session accessible as an attribute of its parent package, so
a regression in the discovery import path fails loudly here rather than
masquerading as a failure in an unrelated test module.
"""
from __future__ import annotations

import sys

import pytest


def _agent_submodule_names() -> list[str]:
    """Return fully-qualified names of imported `agents.<name>.agent` modules."""
    return [name for name in sys.modules if name.startswith("agents.") and name.count(".") == 2]


@pytest.fixture(autouse=True, scope="session")
def _assert_agent_submodules_bound():
    yield

    broken: list[str] = []
    for fqn in _agent_submodule_names():
        _, package, submodule = fqn.split(".")
        parent = f"agents.{package}"
        parent_mod = sys.modules.get(parent)
        if parent_mod is None or getattr(parent_mod, submodule, None) is not sys.modules.get(fqn):
            broken.append(fqn)

    if broken:
        raise AssertionError(
            "Agent submodules not bound to their parent package after discovery; "
            "this breaks mock.patch() in unrelated tests. Broken: "
            + ", ".join(sorted(broken))
        )

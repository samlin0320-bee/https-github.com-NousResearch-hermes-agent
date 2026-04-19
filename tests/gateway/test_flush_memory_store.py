"""Regression test for #6157: flush agent must init _memory_store.

The gateway pre-reset memory flush creates a temporary AIAgent.  If that
agent is created with ``skip_memory=True``, the memory tool is available
(it's in ``enabled_toolsets``) but ``_memory_store`` is ``None``, so every
call returns ``{"success": False, "error": "Memory is not available."}``.

This test verifies that ``skip_memory=False`` is passed so the store is
properly initialised.
"""

import ast
from pathlib import Path


def test_flush_agent_skip_memory_is_false():
    """Parse gateway/run.py AST to verify skip_memory=False in flush agent.

    A static check avoids importing the full dependency tree while
    ensuring the fix for #6157 doesn't regress.
    """
    src = Path(__file__).resolve().parents[2] / "gateway" / "run.py"
    tree = ast.parse(src.read_text())

    # Find the _flush_memories_for_session method
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == "_flush_memories_for_session":
            # Find the AIAgent(...) call inside it
            for child in ast.walk(node):
                if isinstance(child, ast.Call):
                    # Look for keyword skip_memory in any Call node
                    for kw in child.keywords:
                        if kw.arg == "skip_memory":
                            # Must be False (NameConstant or Constant)
                            if isinstance(kw.value, ast.Constant):
                                assert kw.value.value is False, (
                                    f"skip_memory must be False to init _memory_store, "
                                    f"got {kw.value.value!r} (#6157)"
                                )
                                return
                            raise AssertionError(
                                f"skip_memory is not a constant: {ast.dump(kw.value)}"
                            )
            raise AssertionError("skip_memory keyword not found in _flush_memories_for_session")
    raise AssertionError("_flush_memories_for_session function not found in gateway/run.py")

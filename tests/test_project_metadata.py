"""Regression tests for packaging metadata in pyproject.toml."""

from pathlib import Path
import tomllib


def _load_optional_dependencies():
    pyproject_path = Path(__file__).resolve().parents[1] / "pyproject.toml"
    with pyproject_path.open("rb") as handle:
        project = tomllib.load(handle)["project"]
    return project["optional-dependencies"]


def test_matrix_extra_linux_only_in_all():
    """mautrix[encryption] depends on python-olm which is upstream-broken on
    modern macOS (archived libolm, C++ errors with Clang 21+).  The [matrix]
    extra is included in [all] but gated to Linux via a platform marker so
    that ``hermes update`` doesn't fail on macOS."""
    optional_dependencies = _load_optional_dependencies()

    assert "matrix" in optional_dependencies
    # Must NOT be unconditional — python-olm has no macOS wheels.
    assert "hermes-agent[matrix]" not in optional_dependencies["all"]
    # Must be present with a Linux platform marker.
    linux_gated = [
        dep for dep in optional_dependencies["all"]
        if "matrix" in dep and "linux" in dep
    ]
    assert linux_gated, "expected hermes-agent[matrix] with sys_platform=='linux' marker in [all]"


def test_first_party_project_install_flows_do_not_use_all_extras():
    """Hermes-owned install/update docs should use the curated ``--extra all``
    flow, not ``--all-extras``."""
    repo_root = Path(__file__).resolve().parents[1]
    owned_paths = [
        "README.md",
        "CONTRIBUTING.md",
        ".github/workflows/tests.yml",
        "scripts/install.sh",
        "scripts/install.ps1",
        "setup-hermes.sh",
        "website/docs/getting-started/installation.md",
        "website/docs/getting-started/updating.md",
        "website/docs/guides/use-mcp-with-hermes.md",
        "website/docs/user-guide/features/mcp.md",
        "website/docs/reference/faq.md",
        "website/docs/developer-guide/contributing.md",
    ]

    offenders = []
    for rel_path in owned_paths:
        content = (repo_root / rel_path).read_text()
        if "uv sync --locked --all-extras" in content:
            offenders.append(rel_path)

    assert offenders == []
## What does this PR do?

Adds first-class Hermes support for Xiaomi MiMo Token Plan as an OpenAI-compatible API-key provider.

It wires the new provider into Hermes' provider registry, runtime resolution, setup/provider selection flows, model normalization, and model catalog/context lookup so `xiaomi-token-plan` behaves like the other direct API-key providers. The docs now cover the provider in the canonical provider reference, env-var reference, and fallback-provider scope.

## Related Issue

Fixes #

## Type of Change

- [ ] 🐛 Bug fix (non-breaking change that fixes an issue)
- [x] ✨ New feature (non-breaking change that adds functionality)
- [ ] 🔒 Security fix
- [ ] 📝 Documentation update
- [x] ✅ Tests (adding or improving test coverage)
- [ ] ♻️ Refactor (no behavior change)
- [ ] 🎯 New skill (bundled or hub)

## Changes Made

- Added `xiaomi-token-plan` to the provider registry in [`hermes_cli/auth.py`](hermes_cli/auth.py) with:
  - default base URL `https://token-plan-ams.xiaomimimo.com/v1`
  - `XIAOMI_MIMO_TP_API_KEY`
  - fixed base URL, no override env var
- Registered Xiaomi MiMo Token Plan in the provider metadata/alias layers in [`hermes_cli/providers.py`](hermes_cli/providers.py) and [`hermes_cli/model_normalize.py`](hermes_cli/model_normalize.py)
- Added Xiaomi-specific curated models in [`hermes_cli/models.py`](hermes_cli/models.py)
- Added the provider to CLI setup/menu flows in [`hermes_cli/main.py`](hermes_cli/main.py) and [`hermes_cli/setup.py`](hermes_cli/setup.py)
- Added models.dev/provider mapping and context inference support in [`agent/models_dev.py`](agent/models_dev.py) and [`agent/model_metadata.py`](agent/model_metadata.py)
- Added test coverage for registry, auto-detection, runtime resolution, normalization, and setup defaults in:
  - [`tests/hermes_cli/test_api_key_providers.py`](tests/hermes_cli/test_api_key_providers.py)
  - [`tests/hermes_cli/test_model_normalize.py`](tests/hermes_cli/test_model_normalize.py)
  - [`tests/hermes_cli/test_setup_model_selection.py`](tests/hermes_cli/test_setup_model_selection.py)
- Updated docs in [`website/docs/integrations/providers.md`](website/docs/integrations/providers.md), [`website/docs/reference/environment-variables.md`](website/docs/reference/environment-variables.md), and [`website/docs/user-guide/features/fallback-providers.md`](website/docs/user-guide/features/fallback-providers.md) to keep Xiaomi visible only where it is relevant.

## How to Test

1. Set `XIAOMI_MIMO_TP_API_KEY` in your environment.
2. Run `hermes model` and select `Xiaomi MiMo Token Plan`, then choose `mini-v2-pro`.
3. Or run `hermes chat --provider xiaomi-token-plan -m mini-v2-pro` and confirm the provider resolves correctly.
4. The Xiaomi endpoint is fixed, so there is no base URL override to set.

Verification already run locally:
- `source venv/bin/activate && python -m pytest tests/hermes_cli/test_api_key_providers.py tests/hermes_cli/test_model_normalize.py tests/hermes_cli/test_setup_model_selection.py -q`
- Result: `166 passed`
- `source venv/bin/activate && python -m pytest tests/ -q`
- Result: `14 failed, 9608 passed, 25 skipped, 1 xpassed`
- The full suite still has unrelated failures outside this Xiaomi provider work, so the checklist item below remains unchecked.

## Checklist

### Code

- [x] I've read the [Contributing Guide](https://github.com/NousResearch/hermes-agent/blob/main/CONTRIBUTING.md)
- [x] My commit messages follow [Conventional Commits](https://www.conventionalcommits.org/) (`fix(scope):`, `feat(scope):`, etc.)
- [x] I searched for [existing PRs](https://github.com/NousResearch/hermes-agent/pulls) to make sure this isn't a duplicate
- [x] My PR contains **only** changes related to this fix/feature (no unrelated commits)
- [ ] I've run `pytest tests/ -q` and all tests pass
- [x] I've added tests for my changes (required for bug fixes, strongly encouraged for features)
- [x] I've tested on my platform: macOS

### Documentation & Housekeeping

- [x] I've updated relevant documentation (README, `docs/`, docstrings) — or N/A
- [ ] I've updated `cli-config.yaml.example` if I added/changed config keys — or N/A
- [ ] I've updated `CONTRIBUTING.md` or `AGENTS.md` if I changed architecture or workflows — or N/A
- [x] I've considered cross-platform impact (Windows, macOS) per the [compatibility guide](https://github.com/NousResearch/hermes-agent/blob/main/CONTRIBUTING.md#cross-platform-compatibility) — or N/A
- [ ] I've updated tool descriptions/schemas if I changed tool behavior — or N/A

## Screenshots / Logs

Not applicable.

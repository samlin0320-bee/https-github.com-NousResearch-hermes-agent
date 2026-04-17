# Plan: Web3 MCP Server Adapter (Optional Skill Bundle)

**Date**: 2026-04-18  
**Scope**: Ethereum + Solana RPC, wallet-oriented MCP tools, optional install — no Hermes core MCP client edits where possible.

## Step 3 — Cross-repo sniff (record)

| Source | Finding |
|--------|---------|
| `E:\MyPROJECT\NousPR\Master_Ledger.md` | **#029** [songcheng151] OPEN FEAT: `src/hermes_mcp/`, `tools/mcp_tool.py`, tests — MCP TaskGroup / stdio lifecycle. **No** ledger row for Web3 / `web3_adapter` / wallet MCP. |
| `E:\MyPROJECT\NousPR\GitHub_Radar.md` | **#11735** OPEN `feat(mcp): TaskGroup discovery, list_tools backoff...` — same theme as #029. |
| Repo today | `optional-skills/blockchain/{base,solana}` = read/query clients only. `optional-skills/mcp/fastmcp` exists. Hermes registers MCP via `~/.hermes/config.yaml` → `mcp_servers` (`tools/mcp_tool.py`). **No** `src/mcp/adapters/` tree. |

**Decision**: Web3 MCP adapter is **blue ocean** for feature name, but **merge-safety**: do **not** touch `tools/mcp_tool.py` or `src/hermes_mcp/` in the same PR as #029; ship as **optional skill** + standalone MCP stdio entrypoint + docs/examples only.

**Pre-code “免战牌”**: Before `git checkout -b`, append a new row to `Master_Ledger.md` index + detail block (per `.cursorrules` Phase 2 template) claiming `optional-skills/mcp/web3-chain-tools/` (or chosen final slug) as WIP.

**Sync**: From repo root (`Alpha_02` worktree only): `git checkout main` → `git pull E:\MyPROJECT\NousPR\Official_Hermes_Mirror main` — **never** `cd` into `Official_Hermes_Mirror`.

---

## Phase 1 — Preconditions (no business logic)

**[Step 1]** Lock branch after sync  
- **File**: (git only) branch `feat/optional-web3-mcp-chain-tools`  
- **Action**: Create  
- **Details**: After successful mirror pull; no commits yet.  
- **Verification**: `git branch --show-current` prints the feature branch.

**[Step 2]** Claim ledger (免战牌)  
- **File**: `E:\MyPROJECT\NousPR\Master_Ledger.md`  
- **Action**: Modify (append index row + detail block per constitution)  
- **Details**: State WIP on new optional skill path; link branch name; avoid claiming #029 files.  
- **Verification**: New `<span id="NNN">` block exists; index table row added.

**[Step 3]** Optional: load builder doc  
- **File**: `E:\MyPROJECT\NousPR\Nous_Domain_Skills\Hermes_Skill_Builder.md`  
- **Action**: Read  
- **Details**: Align SKILL frontmatter, scripts layout, English-only code/comments.  
- **Verification**: Checklist items from doc satisfied before first commit.

---

## Phase 2 — Optional skill skeleton

**[Step 4]** Create skill root  
- **File**: `optional-skills/mcp/web3-chain-tools/SKILL.md`  
- **Action**: Create  
- **Details**: Triggers, safety warnings (no raw key in logs), dependency on `uv run --with web3,solana` or extras group documented; pointer to MCP stdio command.  
- **Verification**: `hermes skills install official/mcp/web3-chain-tools` path matches catalog convention (verify against existing `official/mcp/fastmcp` pattern).

**[Step 5]** Skill metadata / manifest  
- **File**: `optional-skills/mcp/web3-chain-tools/DESCRIPTION.md` (if category requires; else single SKILL.md only per siblings)  
- **Action**: Create or skip if redundant  
- **Details**: Match sibling `optional-skills/mcp/fastmcp` layout exactly.  
- **Verification**: Directory listing mirrors required optional-skill structure.

**[Step 6]** Document MCP server install (extras)  
- **File**: `pyproject.toml`  
- **Action**: Modify  
- **Details**: Add optional dependency group e.g. `[project.optional-dependencies] web3-mcp = ["web3>=6", "solana", ...]` — exact pins follow upstream style; **no** new hard deps on default install.  
- **Verification**: `pip install -e ".[web3-mcp]"` (or `uv sync --extra web3-mcp`) resolves on CI matrix doc; local dry-run acceptable per `.cursorrules`.

---

## Phase 3 — MCP stdio server (Python)

**[Step 7]** Server entrypoint  
- **File**: `optional-skills/mcp/web3-chain-tools/scripts/web3_mcp_server.py` (name finalizable)  
- **Action**: Create  
- **Details**: Use official `mcp` SDK `Server` + stdio; register tools: `query_balance` (EVM + Solana), `estimate_gas` / `evm_call` (read-only path), `send_raw_transaction` (accept **pre-signed** hex by default), `subscribe_logs` / `monitor_event` (polling or WS with bounded retry). **Default**: refuse raw private keys in tool args; read signing material only from env vars referenced in config doc (`WEB3_PRIVATE_KEY_ENV` etc.).  
- **Verification**: `python scripts/web3_mcp_server.py` starts without error when MCP + extras installed; Ctrl+C clean exit.

**[Step 8]** EVM helper module (keep files &lt;150 lines if constitution mirrored)  
- **File**: `optional-skills/mcp/web3-chain-tools/scripts/evm_tools.py`  
- **Action**: Create  
- **Details**: `Web3` HTTP provider, nonce + gas price helpers, structured JSON errors (no secrets in messages).  
- **Verification**: Unit tests with mocked `web3.eth` (no network).

**[Step 9]** Solana helper module  
- **File**: `optional-skills/mcp/web3-chain-tools/scripts/svm_tools.py`  
- **Action**: Create  
- **Details**: Async-friendly client or sync wrapper clearly documented; balance + simulate + send pre-signed.  
- **Verification**: Mocked RPC tests.

**[Step 10]** Rate limit + approval gateway hooks  
- **File**: `optional-skills/mcp/web3-chain-tools/scripts/rate_limit.py`, `optional-skills/mcp/web3-chain-tools/scripts/approval_hooks.py` (or single `guards.py` if tiny)  
- **Action**: Create  
- **Details**: In-process token bucket per tool name; for `send_*`, optional HTTP callback URL from env `WEB3_APPROVAL_GATEWAY_URL` (POST payload with tx preview) — **timeout + deny-by-default** if unreachable (configurable). Align semantics with existing Hermes dangerous-command approval **documentation only** unless a stable internal API is found (grep `tools/approval.py` before wiring).  
- **Verification**: Tests: bucket blocks burst; mock gateway allow/deny paths.

**[Step 11]** Sandboxed signer interface (no HW in v1)  
- **File**: `optional-skills/mcp/web3-chain-tools/scripts/signer_base.py`  
- **Action**: Create  
- **Details**: `Protocol` / ABC: `sign_evm_transaction`, `sign_solana_message`; impl `EnvPrivateKeySigner` (dev only, documented risk); stub `HardwareWalletSigner` raising `NotImplementedError` with extension docstring.  
- **Verification**: Import test; env signer behind explicit `WEB3_ALLOW_INSECURE_ENV_SIGNER=1`.

---

## Phase 4 — Event-driven queue (bounded, no core agent fork)

**[Step 12]** Persistent queue  
- **File**: `optional-skills/mcp/web3-chain-tools/scripts/event_queue.py`  
- **Action**: Create  
- **Details**: SQLite (`~/.hermes/` subpath documented) or user-configured path; schema: id, chain, payload_json, created_at, status. Tool `monitor_event` enqueues; tool `dequeue_events` returns batch for agent.  
- **Verification**: Tests with temp SQLite.

**[Step 13]** Optional websocket listener subprocess  
- **File**: `optional-skills/mcp/web3-chain-tools/scripts/ws_listener.py`  
- **Action**: Create  
- **Details**: Long-running task: connect WS RPC, filter topics, write to `event_queue`; reconnect with exponential backoff + max retries; **never** auto-invoke Hermes subagent (out of scope for no-core-change PR); document that external automation can poll `dequeue_events`. Phase-2 doc: “subagent trigger” = operator wires gateway cron or future Hermes hook.  
- **Verification**: Mock WS or integration test marked optional (`pytest.importorskip` / live marker).

---

## Phase 5 — Hermes integration surface (docs + examples only)

**[Step 14]** Example `mcp_servers` snippet  
- **File**: `optional-skills/mcp/web3-chain-tools/references/config-snippet.yaml` (or `examples/mcp-web3.yaml`)  
- **Action**: Create  
- **Details**: Show `command` + `args` + `env` for `uv run` with extras; placeholders `ETHEREUM_RPC_URL`, `SOLANA_RPC_URL`, `WEB3_PRIVATE_KEY_ENV` name-only.  
- **Verification**: YAML parses; no real secrets.

**[Step 15]** Catalog + extract script  
- **File**: `website/docs/reference/optional-skills-catalog.md`  
- **Action**: Modify  
- **Details**: New row under **MCP** for `web3-chain-tools`.  
- **Verification**: If repo has `website/scripts/extract-skills.py`, run it per CONTRIBUTING or spot-check generated output.

**[Step 16]** Tests  
- **File**: `tests/skills/test_web3_chain_tools_mcp.py` (path aligned with `tests/skills/test_duckdb_analytics.py` pattern)  
- **Action**: Create  
- **Details**: `pytest.importorskip` for heavy deps; smoke import server module; core pure-logic tests always on.  
- **Verification**: `pytest tests/skills/test_web3_chain_tools_mcp.py -q -o addopts=` passes in CI/local.

---

## Phase 6 — Verification & handoff

**[Step 17]** Test sentinel  
- **Action**: Run `python "C:\Users\Administrator\.cursor\skills\test-sentinel\scripts\test_runner.py"` (or targeted pytest first if sentinel wraps it).  
- **Verification**: Green or documented skip reasons only for optional network.

**[Step 18]** Commit discipline  
- **Action**: `git add` only `optional-skills/...`, `tests/skills/...`, `pyproject.toml`, `website/docs/reference/optional-skills-catalog.md`, `docs/plans/...` if desired — **exclude** `PROJECT_LOG.md`, `.cursorrules`, ledger paths.  
- **Verification**: `git diff --cached` shows no forbidden files.

---

## Risk register

| Risk | Mitigation |
|------|------------|
| #029 merge conflicts if touching `mcp_tool.py` | Zero changes to core MCP client in v1 PR. |
| Private key exfiltration | No keys in tool args; redact errors; env-only signer behind flags; document hardware path as stub. |
| `src/mcp/adapters/web3_adapter.py` requested path | Upstream has no `src/mcp/adapters/`; use optional-skill `scripts/` layout unless architected move — avoids orphan package. |

---

## Plan Master closing

> **Plan Master 提示**: 以上是精确到文件级别的原子化任务拆解。是否合理？如果无误，请回复「按计划执行」，实施代理将按步骤逐一击破并验证。

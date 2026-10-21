---

description: "Task list for the agent harness platform (001)"
---

# Tasks: Plateforme de harnais d'agents

**Input**: Design documents from `/specs/001-agent-harness-platform/`

**Prerequisites**: [plan.md](plan.md), [spec.md](spec.md), [research.md](research.md),
[data-model.md](data-model.md), [contracts/](contracts/), [quickstart.md](quickstart.md)

**Tests**: OBLIGATOIRES. La constitution (principe I, non négociable) impose le test-first :
dans chaque phase, les tâches de test DOIVENT être écrites et en échec avant les tâches
d'implémentation qu'elles couvrent. Aucun test unitaire n'appelle Ollama : utiliser
`FakeProvider` (T030).

**Organization**: Tasks are grouped by user story to enable independent implementation and testing of each story.

## Format: `[ID] [P?] [Story] Description`

- **[P]**: Can run in parallel (different files, no dependencies)
- **[Story]**: Which user story this task belongs to (US1 à US4)
- Chemins : `backend/src/harness/`, `backend/tests/`, `frontend/src/`, `deploy/` (voir plan.md)

---

## Phase 1: Setup (Shared Infrastructure)

**Purpose**: Project initialization and basic structure

- [X] T001 Create the directory layout from plan.md: `backend/src/harness/{db/models,auth,groups,catalog,agents,runtime/middleware,memory,traces,api}`, `backend/tests/{unit,integration,contract,fixtures}`, `backend/evals/`, `backend/alembic/`, `frontend/src/{api,pages,components}`, `frontend/tests/`, `deploy/`, each Python package with an empty `__init__.py`
- [X] T002 Initialize the backend uv project in backend/pyproject.toml (Python 3.12) with pinned runtime deps `deepagents==0.7.29`, `langchain-ollama==1.1.0`, `langchain-mcp-adapters==0.3.2`, `langgraph-checkpoint-postgres==3.2.0`, plus FastAPI, Pydantic 2, pydantic-settings, SQLAlchemy 2 (asyncio), psycopg 3, Alembic, sse-starlette, Authlib, cryptography, structlog, httpx; dev deps pytest, pytest-asyncio, testcontainers[postgres], openapi-core, ruff, mypy; console script `harness-admin = harness.cli:main`; commit backend/uv.lock
- [X] T003 [P] Initialize the frontend in frontend/package.json with Vite + React 18 + TypeScript (`"strict": true` in frontend/tsconfig.json), TanStack Query, React Router, openapi-typescript, vitest, @testing-library/react, jsdom; commit frontend/package-lock.json
- [X] T004 [P] Configure ruff and mypy (strict) in backend/pyproject.toml, and ESLint + Prettier in frontend/eslint.config.js and frontend/.prettierrc
- [X] T005 [P] Create deploy/docker-compose.yml with services `postgres` (postgres:16), `ollama`, `backend`, `frontend` and the local OIDC test IdP `dex` (image `ghcr.io/dexidp/dex:v2.41.1`, static test users only), and deploy/.env.example listing `DATABASE_URL`, `HARNESS_ENCRYPTION_KEY`, `OIDC_ISSUER`, `OIDC_CLIENT_ID`, `OIDC_CLIENT_SECRET`, `OLLAMA_BASE_URL`, `LANGSMITH_TRACING=false` (no real secret values)
- [X] T006 [P] Create deploy/backend.Dockerfile and deploy/frontend.Dockerfile (multi-stage, non-root user, no secrets baked in, config only from environment)
- [X] T007 [P] Create .github/workflows/ci.yml running `uv sync --frozen`, ruff, mypy, pytest in backend/ and `npm ci`, lint, `npm test` in frontend/, with third-party actions pinned by commit SHA and `pip-audit` / `npm audit --audit-level=high` steps

---

## Phase 2: Foundational (Blocking Prerequisites)

**Purpose**: Core infrastructure that MUST be complete before ANY user story can be implemented

**⚠️ CRITICAL**: No user story work can begin until this phase is complete

### Tests for Foundation (write first, must fail)

- [X] T008 Create backend/tests/conftest.py with fixtures: PostgreSQL 16 via testcontainers, `alembic upgrade head`, async SQLAlchemy session, httpx `AsyncClient` on the FastAPI app, and `login_as(person, group, roles)` that overrides the auth dependency
- [X] T009 [P] Write backend/tests/unit/test_config.py: settings fail to load without `HARNESS_ENCRYPTION_KEY`; `LANGSMITH_TRACING` is forced to `false` in `os.environ` after import of `harness.config` even if set to `true`
- [X] T010 [P] Write backend/tests/integration/test_rls_isolation.py: for every table using `GroupScopedMixin` (discovered from SQLAlchemy metadata), rows inserted under group A are invisible and not updatable when the transaction runs `SET LOCAL app.group_id` to group B, and invisible when `app.group_id` is unset
- [X] T011 [P] Write backend/tests/unit/test_permissions.py: role matrix for `admin`, `tool_manager`, `group_memory_manager`, `approver`, `member` with cumulative roles; a non-member gets 404 (never 403) on any `/groups/{groupId}/...` route; a person with `is_platform_admin` and no membership gets 404 on group content (FR-022a)
- [X] T012 [P] Write backend/tests/unit/test_redaction.py: known group secrets, bearer tokens, API-key patterns, e-mail addresses and phone numbers are replaced by `«masqué»` in nested dicts, lists and strings; non-sensitive text is unchanged
- [X] T013 [P] Write backend/tests/unit/test_crypto.py: AES-GCM encrypt/decrypt round trip with `key_version`, tampered ciphertext raises, and the plaintext never appears in `repr()` or `str()` of the credential value object
- [X] T014 [P] Write backend/tests/contract/conftest.py loading specs/001-agent-harness-platform/contracts/openapi.yaml with openapi-core and exposing `assert_matches_contract(response)` used by all contract tests

### Implementation for Foundation

- [X] T015 Implement backend/src/harness/config.py with pydantic-settings (`DATABASE_URL`, `HARNESS_ENCRYPTION_KEY`, OIDC settings, `OLLAMA_BASE_URL`) and force `os.environ["LANGSMITH_TRACING"] = "false"` at import (makes T009 pass)
- [X] T016 Implement backend/src/harness/db/base.py: declarative `Base` with `id` (UUID), `created_at`, `updated_at`, and `GroupScopedMixin` adding non-null indexed `group_id`
- [X] T017 Implement backend/src/harness/db/session.py: async engine and session factory, and `group_context(session, group_id)` executing `SET LOCAL app.group_id = :group_id` at transaction start
- [X] T018 Initialize Alembic in backend/alembic/ (async env) with a helper `enable_rls(table)` creating policy `group_id = current_setting('app.group_id', true)::uuid` with `FORCE ROW LEVEL SECURITY`, and a migration step creating the application role without `BYPASSRLS`
- [X] T019 [P] Create Person, Group and Membership models in backend/src/harness/db/models/identity.py: Person `oidc_subject` (unique), `email`, `display_name`, `is_platform_admin`; Group `name` (unique), `status` : `active` | `disabled`, `trace_retention_days` (90), `approval_timeout_hours` (24), `proposal_expiry_days` (30), `cost_per_1k_tokens` (0); Membership `group_id`, `person_id` (unique ensemble), `roles` subset of `admin`, `tool_manager`, `group_memory_manager`, `approver`, `member`, `max_concurrent_runs`, `max_tokens_per_day`, `status` : `active` | `removed`
- [X] T020 [P] Create AdminAuditEntry model in backend/src/harness/db/models/audit.py (`group_id` nullable for platform actions, `actor_id`, `action`, `target_type`, `target_id`, `details` JSON without secrets, `occurred_at`) and `record_admin_action()` in backend/src/harness/groups/audit.py
- [X] T021 Create migration backend/alembic/versions/0001_identity.py for T019–T020 tables, enabling RLS on Membership and AdminAuditEntry (makes T010 pass for these tables)
- [X] T022 [P] Implement backend/src/harness/traces/redaction.py `redact(value, known_secrets)` (makes T012 pass) and a structlog processor using it
- [X] T023 [P] Implement backend/src/harness/catalog/crypto.py: AES-GCM with key from `HARNESS_ENCRYPTION_KEY`, `key_version`, opaque `SecretValue` type with masked `__repr__` (makes T013 pass)
- [X] T024 Implement backend/src/harness/auth/oidc.py with Authlib (login, callback, logout), session cookie `harness_session` (HttpOnly, Secure, SameSite=Lax), upsert of Person by `oidc_subject`
- [X] T025 Implement backend/src/harness/auth/permissions.py: dependencies `current_principal` (person or service account), `group_member(groupId)` returning 404 for non-members, `require_roles(*roles)` returning 403, and opening `group_context` for the request (makes T011 pass)
- [X] T026 Implement backend/src/harness/main.py: FastAPI app, `/api/v1` router, JSON structlog logging with the redaction processor, exception handlers returning the `Error` schema `{code, message}`, lifespan hooks for startup tasks
- [X] T027 [P] Implement `GET /api/v1/me` in backend/src/harness/api/me.py returning identity and memberships (schema `Me`, FR-022b)
- [X] T028 [P] Implement backend/src/harness/cli.py `harness-admin create-group <name> --admin <email>` creating the group and admin membership and recording an AdminAuditEntry
- [X] T029 [P] Write backend/tests/unit/test_model_provider.py: `FakeProvider` replays scripted messages and tool calls; `OllamaProvider.check_tool_calling(model_id)` returns false when the model reply has no tool call (Ollama client mocked)
- [X] T030 Implement backend/src/harness/runtime/models.py: `ModelProvider` protocol, `OllamaProvider` (`ChatOllama`, `OLLAMA_BASE_URL`), `FakeProvider` wrapping `GenericFakeChatModel`, and `check_tool_calling(model_id)` (makes T029 pass)
- [X] T031 [P] Generate the typed client in frontend/src/api/schema.d.ts from contracts/openapi.yaml (`npm run gen:api`) and a fetch wrapper with credentials and `Error` handling in frontend/src/api/client.ts
- [X] T032 [P] Implement frontend/src/App.tsx (router, auth guard calling `/me`, redirect to OIDC login) and frontend/src/components/GroupSwitcher.tsx showing the current group at all times (FR-022b)

**Checkpoint**: Foundation ready - user story implementation can now begin in parallel

---

## Phase 3: User Story 1 - Lancer un agent et suivre son exécution (Priority: P1) 🎯 MVP

**Goal**: un membre crée un agent, le lance sur une tâche, suit chaque étape en direct, l'arrête,
voit les limites appliquées, les outils non autorisés refusés et les outils à effet externe mis
en attente d'approbation ; la trace reste consultable.

**Independent Test**: quickstart S1 et S2 : agent avec un outil `read_only` d'un serveur MCP de
test, exécution tracée en < 2 s par étape, arrêt < 5 s, `limit_reached`, `tool_call_refused`,
`approval_requested` puis décision par une personne habilitée.

### Tests for User Story 1 (write first, must fail)

- [X] T033 [P] [US1] Create backend/tests/fixtures/mcp_test_server.py: in-process MCP server over streamable HTTP exposing `lookup` (returns text) and `send_message` (records calls), plus a slow tool `wait(seconds)`
- [X] T034 [P] [US1] Write backend/tests/contract/test_tools_mcp_contract.py for `POST/GET /groups/{groupId}/tools` (kind `mcp_server`) and `PUT .../operations/{operationName}/classification`
- [X] T035 [P] [US1] Write backend/tests/contract/test_agents_contract.py for agents CRUD and `POST .../duplicate`; FR-001a: any member can read, launch and duplicate an agent, while a member who is neither its `created_by` nor `admin` gets 403 on update and delete
- [X] T036 [P] [US1] Write backend/tests/contract/test_runs_contract.py for `POST/GET /runs`, `GET /runs/{runId}`, `POST /runs/{runId}/stop`, `GET /runs/{runId}/events` (JSON)
- [X] T037 [P] [US1] Write backend/tests/contract/test_approvals_contract.py for `GET /approvals` and `POST /approvals/{approvalId}/decision`
- [X] T038 [P] [US1] Write backend/tests/unit/test_builder.py: only tools in `allowed_tool_ids` are bound; `interrupt_on` maps `external_effect` and `unclassified` operations to `{"allowed_decisions": ["approve", "reject"]}` and omits `read_only` ones; subagents inherit the same tools, middleware and `interrupt_on`; the effective internal tools are exactly `ls`, `read_file`, `glob`, `grep`, `write_file`, `edit_file`, `delete` (FR-006a), `task` is bound only when `subagents_enabled` is true, and no other framework tool (`execute`, `write_todos`) is bound
- [X] T039 [P] [US1] Write backend/tests/unit/test_allowlist_middleware.py: a tool call outside the allowlist or with an unknown tool name is not executed and emits `tool_call_refused` with reason `not_allowed` or `unknown_tool`; an internal tool from the FR-006a list is accepted and a framework tool outside it (e.g. `execute`) is refused
- [X] T040 [P] [US1] Write backend/tests/unit/test_budget_middleware.py: exceeding `max_iterations`, `max_duration_seconds` or `max_tokens` stops the run with `limit_reached` (`limit` = `iterations` / `duration` / `tokens`)
- [X] T041 [P] [US1] Write backend/tests/unit/test_trace_middleware.py: events `model_step`, `tool_call_started`, `tool_call_finished` are emitted with contiguous `seq` starting at 1 and redacted payloads; `run_started` carries `system_prompt` and `internal_tools`, each `model_step` carries masked `input_messages`, concatenating them reproduces the full model context of step N, and any text field over 64 KB is truncated with `truncated: true` and its `sha256`
- [X] T042 [P] [US1] Write backend/tests/unit/test_untrusted_content.py: tool results are wrapped as untrusted data before reaching the model; an instruction embedded in a tool result does not change the system prompt or the bound tools
- [X] T043 [P] [US1] Write backend/tests/integration/test_run_lifecycle.py with FakeProvider and the MCP test server: run `succeeded` with `run_started` … `run_ended`; stop request yields `stopped` within 5 s; `max_iterations=1` yields `limit_reached`; refused tool is traced; an MCP server that goes down mid-run yields `tool_call_finished` with `status = error` and an explicit `error`, and the run continues or ends without hanging
- [X] T044 [P] [US1] Write backend/tests/integration/test_approvals.py: calling `send_message` (unclassified) moves the run to `awaiting_approval` and creates an ApprovalRequest; another member without `approver` gets 403; the launcher or an `approver` decides; `approve` executes the call, `reject` skips it; the decision and `decided_by` appear in `approval_decided`; a request past `expires_at` becomes `expired` and the run ends `expired`; for a run launched by a service account in a group with no `approver`, the request is flagged `no_eligible_approver = true`, is visible read-only to the group admins, expires at the timeout and the run ends `expired`
- [X] T045 [P] [US1] Write backend/tests/integration/test_restart_recovery.py (SC-011): after simulated restart, `running` runs become `interrupted` with `run_ended` `stop_reason = platform_restart`; `awaiting_approval` runs stay and resume after decision; `relaunch_of` creates a new run with `relaunched_from`
- [X] T046 [P] [US1] Write backend/tests/integration/test_trace_stream.py: SSE stream delivers each event < 2 s after it is written, reconnect with `Last-Event-ID` gives no loss and no duplicate, stream closes after `run_ended`
- [X] T047 [P] [US1] Write frontend/tests/LiveTrace.test.tsx: renders streamed events in `seq` order, shows status changes, and the stop button calls `POST /runs/{runId}/stop`

### Implementation for User Story 1

- [X] T048 [P] [US1] Create Tool and ToolOperation models in backend/src/harness/db/models/catalog.py: Tool `kind` : `mcp_server` | `skill` | `hook`, `name` (unique par groupe et type), `description`, `status` : `active` | `retired`, `created_by`, MCP fields `url` (HTTPS, transport distant uniquement), `last_check_at`, `last_check_ok`; ToolOperation `tool_id`, `operation_name`, `input_schema`, `classification` : `read_only` | `external_effect` | `unclassified` (traité comme `external_effect`)
- [X] T049 [P] [US1] Create Agent model in backend/src/harness/db/models/agents.py: `name`, `instructions`, `model_id`, `created_by`, `allowed_tool_ids`, `agent_memory_enabled`, `group_memory_enabled`, `subagents_enabled` (faux par défaut), `max_iterations`, `max_duration_seconds`, `max_tokens`, `config_changed_at`
- [X] T050 [P] [US1] Create Run, TraceEvent and ApprovalRequest models in backend/src/harness/db/models/runs.py: Run `status` in `queued`, `running`, `awaiting_approval`, `succeeded`, `failed`, `stopped`, `limit_reached`, `interrupted`, `expired`, `rejected_quota`, `stop_reason`, `agent_snapshot`, `task`, `triggered_by_type` : `person` | `service_account`, `triggered_by_id`, `launch_mode` : `manual` | `programmatic`, `thread_id`, `relaunched_from`, `iterations`, `input_tokens`, `output_tokens`, `cost`, `started_at`, `ended_at`, `result`; TraceEvent `run_id`, `seq` (unique par run), `occurred_at`, `type`, `payload`, index (`group_id`, `run_id`, `seq`); ApprovalRequest `run_id`, `tool_operation`, `arguments` (masqués), `requested_at`, `expires_at`, `status` : `pending` → `approved` | `rejected` | `expired`, `decided_by`, `decided_at`
- [X] T051 [US1] Create migration backend/alembic/versions/0002_agents_runs.py for T048–T050 with RLS enabled on every new table
- [X] T052 [US1] Implement backend/src/harness/catalog/mcp.py: register an MCP server (reject non-HTTPS URLs and any stdio transport), check reachability and list operations via `MultiServerMCPClient`, store ToolOperations as `unclassified`; `classify(operation, classification)` restricted to `tool_manager`; record an AdminAuditEntry for every registration and classification change (FR-024)
- [X] T053 [US1] Implement MCP registration, listing, detail and classification routes in backend/src/harness/api/tools.py (makes T034 pass; 422 when unreachable), each call recording its AdminAuditEntry through T052
- [X] T054 [US1] Implement backend/src/harness/agents/service.py (create, update, delete, duplicate; validate `model_id` with `check_tool_calling`, all `allowed_tool_ids` active in the same group) and routes in backend/src/harness/api/agents.py (makes T035 pass); enforce FR-001a: update and delete only by `created_by` or `admin`
- [X] T055 [US1] Implement backend/src/harness/traces/writer.py (append event with gap-free `seq` per run, redaction before insert) and backend/src/harness/traces/bus.py (in-process pub/sub per `run_id`)
- [X] T056 [P] [US1] Implement backend/src/harness/runtime/middleware/trace.py emitting the events listed in contracts/trace-events.md (makes T041 pass), including `system_prompt`, `internal_tools`, `input_messages` and the 64 KB truncation rule
- [X] T057 [P] [US1] Implement backend/src/harness/runtime/middleware/allowlist.py (makes T039 pass)
- [X] T058 [P] [US1] Implement backend/src/harness/runtime/middleware/budget.py counting iterations and tokens from model responses and enforcing `max_duration_seconds` (makes T040 pass)
- [X] T059 [P] [US1] Implement backend/src/harness/runtime/middleware/untrusted_content.py (makes T042 pass)
- [X] T060 [US1] Implement backend/src/harness/runtime/builder.py: `build_agent(agent_snapshot, provider, mcp_tools, checkpointer, backend_factory)` calling `create_deep_agent` with allowed tools only, `interrupt_on` from classifications, middleware in order trace → allowlist → hooks (no-op until US2) → budget → untrusted-content, an explicit `FilesystemMiddleware` allowlist `ls`, `read_file`, `glob`, `grep`, `write_file`, `edit_file`, `delete`, the `task` subagent only when `subagents_enabled`, and `StateBackend` default (makes T038 pass)
- [X] T061 [US1] Implement backend/src/harness/runtime/persistence.py: `AsyncPostgresSaver` only (no LangGraph store), in schema `langgraph`, set up at app startup
- [X] T062 [US1] Implement backend/src/harness/runtime/runner.py: `launch` (snapshot agent, create Run `queued` → `running`, global concurrency semaphore, asyncio task with `recursion_limit` and timeout), `stop` (cancel, checked between steps), status transitions per data-model.md, `run_started` / `run_ended` events, cost from `Group.cost_per_1k_tokens` (makes T043 pass)
- [X] T063 [US1] Implement backend/src/harness/runtime/approvals.py: on `__interrupt__` create ApprovalRequest with `expires_at = requested_at + Group.approval_timeout_hours`, set run `awaiting_approval`, emit `approval_requested`; `decide()` allowed for the launching person or a member with `approver`, never a service account; resume with `Command(resume={"decisions": [...]})`; periodic expiry task (makes T044 pass); when no eligible approver exists, set `no_eligible_approver = true` on the request and expose it to group admins
- [X] T064 [US1] Implement startup recovery in backend/src/harness/runtime/runner.py called from the lifespan in backend/src/harness/main.py, and relaunch support via `relaunch_of` (makes T045 pass)
- [X] T065 [US1] Implement routes in backend/src/harness/api/runs.py (list with filters `agent_id`, `triggered_by`, `status`, `from`, `to`, cursor; create with `launch_mode = manual`; get; stop; events as JSON or SSE with `Last-Event-ID`) and backend/src/harness/api/approvals.py (makes T036, T037, T046 pass)
- [X] T066 [P] [US1] Implement frontend/src/pages/AgentsPage.tsx and frontend/src/components/AgentForm.tsx (name, instructions, model, allowed tools, limits, memory toggles hidden until US3) with an empty-state guide for the first agent (SC-001)
- [X] T067 [P] [US1] Implement frontend/src/components/LiveTrace.tsx (EventSource, reconnect with `Last-Event-ID`) and frontend/src/pages/RunPage.tsx with stop and relaunch buttons (makes T047 pass)
- [X] T068 [P] [US1] Implement frontend/src/pages/RunsPage.tsx with filters by agent, triggerer, status and period (FR-008)
- [X] T069 [P] [US1] Implement frontend/src/pages/ApprovalsPage.tsx and a pending-approvals badge in frontend/src/components/ApprovalBadge.tsx polling `GET /approvals?status=pending` (in-app notification for FR-005b), with a distinct warning for admins on requests flagged `no_eligible_approver`
- [X] T070 [P] [US1] Write backend/tests/unit/test_eval_runner.py: a suite in `backend/evals/suites/<name>/` (YAML tasks with expected outcome) runs deterministically against `FakeProvider`, and the runner writes a Markdown report with the success rate per task, plus a before/after comparison when given `--baseline <report>`
- [X] T071 [US1] Implement backend/evals/run.py and the core suite backend/evals/suites/core/ (allowed tool use, refused tool, approval pause, limit reached, stop), and commit the baseline report backend/evals/reports/core-baseline.md produced with the target Ollama model (makes T070 pass; constitution V)

**Checkpoint**: At this point, User Story 1 should be fully functional and testable independently

---

## Phase 4: User Story 2 - Gérer le catalogue d'outils : MCP, skills et hooks (Priority: P2)

**Goal**: les gestionnaires d'outils enregistrent serveurs MCP (avec identifiants protégés),
skills et hooks ; les hooks règles ou webhooks autorisent, bloquent ou journalisent ; le retrait
d'un outil est signalé.

**Independent Test**: quickstart S3 : hook règle qui bloque `external_effect`, webhook muet bloqué
après 10 s, skill chargée et visible dans la trace, identifiant MCP absent de toutes les sorties.

### Tests for User Story 2 (write first, must fail)

- [ ] T072 [P] [US2] Write backend/tests/contract/test_tools_catalog_contract.py for `POST /tools` with kinds `skill` and `hook`, `DELETE /tools/{toolId}`, and `Tool.has_credential` without any credential value
- [ ] T073 [P] [US2] Write backend/tests/unit/test_hook_rules.py: conditions on agent, tool, classification, triggerer and argument pattern; decisions `allow` | `block` | `log`; evaluation by `priority`, `block` wins over `allow`
- [ ] T074 [P] [US2] Write backend/tests/unit/test_hook_webhook.py: request carries `X-Harness-Event`, `X-Harness-Delivery`, `X-Harness-Timestamp`, `X-Harness-Signature: sha256=<hmac>`; body matches contracts/hook-webhook.md and contains no credential and no memory content; timeout of 10 s, network error, non-200 status, invalid body or unknown decision all yield `block`
- [ ] T075 [P] [US2] Write backend/tests/unit/test_hooks_middleware.py: hooks fire on `run_start`, `run_end`, `before_tool_call`, `after_tool_call`; a `block` before a call prevents it and emits `hook_evaluated`; `block` on `after_tool_call` / `run_end` is only logged
- [ ] T076 [P] [US2] Write backend/tests/integration/test_credentials_secrecy.py (SC-005): register an MCP server with a credential, run an agent using it; the credential value never appears in any API response, captured log line or TraceEvent payload, and is sent only to the MCP server
- [ ] T077 [P] [US2] Write backend/tests/integration/test_skills.py: a skill without `SKILL.md` frontmatter `name`/`description` is rejected (422); an allowed skill appears by name in the agent prompt, its `SKILL.md` is read via `read_file` and traced; no script in the skill is executed
- [ ] T078 [P] [US2] Write backend/tests/integration/test_tool_retirement.py: retiring a tool sets `config_changed_at` on agents using it; the next run executes without it and emits `config_warning`
- [ ] T079 [P] [US2] Write frontend/tests/ToolsPage.test.tsx: credential input is write-only and never pre-filled; classification can be changed per operation

### Implementation for User Story 2

- [ ] T080 [P] [US2] Create Credential and HookDefinition models in backend/src/harness/db/models/catalog.py: Credential `ciphertext`, `nonce`, `key_version`; Tool `credential_id` (optionnel), skill fields `skill_name`, `skill_description`, `files`; HookDefinition `events` subset of `run_start`, `run_end`, `before_tool_call`, `after_tool_call`, `hook_type` : `rule` | `webhook`, `condition`, `decision` : `allow` | `block` | `log`, `url` (HTTPS), `signing_secret_id`, `timeout_seconds` (10), `priority`
- [ ] T081 [US2] Create migration backend/alembic/versions/0003_catalog_extensions.py for T080 with RLS on new tables
- [ ] T082 [US2] Implement backend/src/harness/catalog/credentials.py (store encrypted, never serialize, decrypt only for runtime injection) and inject MCP credentials as request headers in backend/src/harness/catalog/mcp.py at run time only (makes T076 pass)
- [ ] T083 [P] [US2] Implement backend/src/harness/catalog/skills.py: validate `SKILL.md` frontmatter, store files in `Tool.files` (served read-only under `/skills/` by `SqlBackend`), classification `read_only`
- [ ] T084 [P] [US2] Implement the rule engine in backend/src/harness/catalog/hook_rules.py (makes T073 pass)
- [ ] T085 [P] [US2] Implement the webhook client in backend/src/harness/catalog/hook_webhook.py per contracts/hook-webhook.md with fail-safe `block` (makes T074 pass)
- [ ] T086 [US2] Implement backend/src/harness/runtime/middleware/hooks.py using T084–T085, emitting `hook_evaluated`, and wire it in backend/src/harness/runtime/builder.py (makes T075 pass)
- [ ] T087 [US2] Extend backend/src/harness/runtime/builder.py to pass allowed skills as `skills` sources served read-only by `SqlBackend` under `/skills/` (makes T077 pass)
- [ ] T088 [US2] Implement retirement in backend/src/harness/catalog/service.py (status `retired`, flag agents, `config_warning` at next run) (makes T078 pass)
- [ ] T089 [US2] Extend backend/src/harness/api/tools.py with skill and hook creation, `DELETE`, credential write-only handling and `has_credential`, recording AdminAuditEntry for every catalog change (FR-024) (makes T072 pass)
- [ ] T090 [P] [US2] Implement frontend/src/pages/ToolsPage.tsx with forms for MCP server (URL, write-only credential), skill (file upload), hook (rule builder or webhook URL + secret) and a per-operation classification editor (makes T079 pass)

**Checkpoint**: At this point, User Stories 1 AND 2 should both work independently

---

## Phase 5: User Story 3 - Donner une mémoire persistante aux agents (Priority: P3)

**Goal**: mémoire d'agent persistante et éditable, mémoire de groupe en lecture pour les agents,
alimentée uniquement par des propositions validées, avec historique et origine tracée.

**Independent Test**: quickstart S4 : préférence retenue puis réutilisée, proposition de groupe
invisible avant acceptation, entrée de groupe lue par un autre agent, agent dupliqué sans mémoire.

### Tests for User Story 3 (write first, must fail)

- [ ] T091 [P] [US3] Write backend/tests/contract/test_memory_contract.py for agent memory, group memory, `PUT/DELETE /memory/{entryId}` (409 on version conflict), revisions, proposals and proposal decision routes; FR-016: agent memory is readable by any member who can launch the agent, a member who is neither the agent's `created_by` nor `admin` gets 403 on update and delete, and only `group_memory_manager` can write group memory
- [ ] T092 [P] [US3] Write backend/tests/unit/test_memory_backend.py: `/memories/` maps to `SqlBackend` entries with `scope = agent` of the current agent, read-write; `/group-memory/` maps to entries with `scope = group` and rejects writes; `/skills/` is read-only; other paths use `StateBackend`; an agent never reads another agent's entries; a read under another group's `app.group_id` returns nothing (RLS)
- [ ] T093 [P] [US3] Write backend/tests/unit/test_propose_group_memory.py: the tool creates a `pending` GroupMemoryProposal and emits `group_memory_proposed` without writing to group memory
- [ ] T094 [P] [US3] Write backend/tests/integration/test_agent_memory.py: a fact written in run 1 (`memory_written`) is read in run 2 (`memory_read` with `scope = agent`); deleting it by a member stops its reuse; every change creates a MemoryRevision
- [ ] T095 [P] [US3] Write backend/tests/integration/test_group_memory.py (SC-010): nothing reaches group memory before acceptance by a `group_memory_manager`; accepted entry is readable by another agent with `group_memory_enabled`; proposals older than `proposal_expiry_days` become `expired`
- [ ] T096 [P] [US3] Write backend/tests/integration/test_memory_conflict.py: two concurrent writes on the same entry keep both versions, the second gets `memory_conflict` and a `conflict` revision; no write is lost
- [ ] T097 [P] [US3] Write backend/tests/integration/test_duplicate_agent_memory.py: a duplicated agent starts with empty agent memory and keeps group-memory access
- [ ] T098 [P] [US3] Write backend/tests/unit/test_memory_untrusted.py (FR-018): memory content containing instructions is wrapped as untrusted data and never alters instructions or allowed tools

### Implementation for User Story 3

- [ ] T099 [P] [US3] Create MemoryEntry, MemoryRevision and GroupMemoryProposal models in backend/src/harness/db/models/memory.py: MemoryEntry `scope` : `agent` | `group`, `agent_id` (si `scope = agent`), `path`, `content`, `version` (verrou optimiste), `source_run_id`, `author_type` : `agent` | `person`, `author_id`; MemoryRevision `version`, `previous_content`, `changed_by`, `changed_at`, `change_type` : `create` | `update` | `delete` | `conflict`; GroupMemoryProposal `run_id`, `agent_id`, `proposed_path`, `proposed_content`, `status` : `pending` → `accepted` | `rejected` | `expired`, `decided_by`, `decided_at`
- [ ] T100 [US3] Create migration backend/alembic/versions/0004_memory.py for T099 with RLS
- [ ] T101 [US3] Implement backend/src/harness/memory/service.py: write with optimistic `version`, MemoryRevision on every change, events `memory_read`, `memory_written`, `memory_conflict` with scope (makes T094, T096 pass)
- [ ] T102 [US3] Implement backend/src/harness/memory/backends.py: `SqlBackend` implementing the Deep Agents backend protocol over MemoryEntry / MemoryRevision (through T101) and `Tool.files`, then the `CompositeBackend` factory with routes `/memories/`, read-only `/group-memory/` and `/skills/`, default `StateBackend` (makes T092 pass)
- [ ] T103 [US3] Implement backend/src/harness/memory/proposals.py: `propose_group_memory` tool, `decide()` restricted to `group_memory_manager` writing via T101 on `accepted`, periodic expiry job (makes T093, T095 pass)
- [ ] T104 [US3] Extend backend/src/harness/runtime/builder.py to configure memory routes from `agent_memory_enabled` / `group_memory_enabled`, add the proposal tool when group memory is enabled, and apply untrusted-content wrapping to memory reads (makes T098 pass)
- [ ] T105 [US3] Ensure duplication in backend/src/harness/agents/service.py creates no agent-memory entries (makes T097 pass)
- [ ] T106 [US3] Implement routes in backend/src/harness/api/memory.py per contracts/openapi.yaml (makes T091 pass); enforce FR-016 rights (agent memory: read by any member who can launch the agent, write by `created_by` or `admin`; group memory: write by `group_memory_manager`)
- [ ] T107 [P] [US3] Implement frontend/src/pages/AgentMemoryPage.tsx (view, edit with version, delete, history) and show memory toggles in frontend/src/components/AgentForm.tsx
- [ ] T108 [P] [US3] Implement frontend/src/pages/GroupMemoryPage.tsx (entries, add entry, pending proposals with accept / reject)
- [ ] T109 [US3] Add the memory evaluation suite in backend/evals/suites/memory/ (versioned tasks with expected outcomes) to the existing runner backend/evals/run.py and commit its with-memory vs without-memory report in backend/evals/reports/memory.md (SC-006)

**Checkpoint**: All user stories so far should now be independently functional

---

## Phase 6: User Story 4 - Travailler en groupe (Priority: P4)

**Goal**: administration multi-groupes (création, désactivation), membres et rôles, quotas,
comptes de service pour le lancement programmatique, journal d'administration, isolation totale.

**Independent Test**: quickstart S5 : 404 sur toute ressource d'un autre groupe, compte de service
qui lance mais ne peut pas approuver, révocation qui arrête ses exécutions, quota → 429.

### Tests for User Story 4 (write first, must fail)

- [ ] T110 [P] [US4] Write backend/tests/contract/test_admin_contract.py for `/admin/groups`, `/admin/groups/{groupId}/disable`, members, service accounts (token returned only at creation) and `/audit`
- [ ] T111 [P] [US4] Write backend/tests/integration/test_cross_group_api.py (SC-009): a member of group B gets 404 on every group-scoped GET/PUT/DELETE/POST route of group A (agents, tools, runs, events, approvals, memory, proposals, members, service accounts, audit), routes enumerated from the FastAPI app
- [ ] T112 [P] [US4] Write backend/tests/integration/test_service_accounts.py: token stored hashed with salt and shown once; launch allowed only on `allowed_agent_ids`, with `launch_mode = programmatic`; status, stop and events reachable by the program (FR-002b); approval decision by the token gets 403; revocation stops its runs and later calls get 401
- [ ] T113 [P] [US4] Write backend/tests/integration/test_quotas.py: exceeding `max_concurrent_runs` or `max_tokens_per_day` returns 429 and records a run `rejected_quota`
- [ ] T114 [P] [US4] Write backend/tests/integration/test_membership_lifecycle.py: removing a member stops their running runs and denies access; a person in two groups has only the roles of each group; disabling a group stops its runs, blocks access and keeps data; every admin action creates an AdminAuditEntry with author and date
- [ ] T115 [P] [US4] Write frontend/tests/GroupAdminPage.test.tsx: service-account token displayed once then hidden; roles editable as a multi-select

### Implementation for User Story 4

- [ ] T116 [P] [US4] Create ServiceAccount model in backend/src/harness/db/models/identity.py: `name`, `token_hash`, `token_prefix`, `status` : `active` | `revoked`, `allowed_agent_ids`, `max_concurrent_runs`, `max_tokens_per_day`
- [ ] T117 [US4] Create migration backend/alembic/versions/0005_service_accounts.py with RLS
- [ ] T118 [US4] Implement backend/src/harness/auth/service_accounts.py (token generation, `token_prefix`, salted SHA-256 hash, bearer authentication in `current_principal`, revoke stopping runs) and extend backend/src/harness/api/runs.py to set `launch_mode = programmatic` for service accounts (makes T112 pass)
- [ ] T119 [US4] Implement backend/src/harness/groups/service.py: invite, update roles and quotas, remove (stop runs), platform-level create and disable group (stop runs), all recorded with `record_admin_action` (makes T114 pass)
- [ ] T120 [US4] Enforce quotas in backend/src/harness/runtime/runner.py before launch (`rejected_quota`, HTTP 429) (makes T113 pass)
- [ ] T121 [US4] Implement routes in backend/src/harness/api/admin.py, backend/src/harness/api/members.py, backend/src/harness/api/service_accounts.py and backend/src/harness/api/audit.py (makes T110, T111 pass)
- [ ] T122 [P] [US4] Implement frontend/src/pages/GroupAdminPage.tsx (members, roles, quotas, service accounts) and frontend/src/pages/AuditPage.tsx (makes T115 pass)
- [ ] T123 [P] [US4] Implement frontend/src/pages/PlatformAdminPage.tsx (create and disable groups, designate first admins) visible only to `is_platform_admin`

**Checkpoint**: All user stories should now be independently functional

---

## Phase 7: Polish & Cross-Cutting Concerns

**Purpose**: Improvements that affect multiple user stories

- [ ] T124 [P] Implement the retention purge job in backend/src/harness/traces/retention.py deleting TraceEvents and memory revisions older than `Group.trace_retention_days`, with backend/tests/integration/test_retention.py written first
- [ ] T125 [P] Write and pass backend/tests/integration/test_concurrency.py (SC-007, SC-002, SC-003): 20 concurrent runs in one group with FakeProvider, each step visible < 2 s and stop < 5 s in 99 % of cases
- [ ] T126 [P] Create backend/evals/load_ollama.py measuring tokens/s and latency for N parallel generations against the target Ollama host, to size hardware or decide on vLLM (research R-10)
- [ ] T127 [P] Write README.md at the repository root: purpose, architecture summary, local start with deploy/docker-compose.yml, test and eval commands, link to specs/001-agent-harness-platform/
- [ ] T128 Run a security review of the whole diff (secrets in logs, RLS coverage on every group table, outbound calls limited to registered MCP servers and hook webhooks, no stdio MCP, no script execution), record findings in specs/001-agent-harness-platform/checklists/security-review.md and fix them with regression tests
- [ ] T129 Run every scenario of specs/001-agent-harness-platform/quickstart.md (S1 to S6 and the memory eval) against deploy/docker-compose.yml and record results in specs/001-agent-harness-platform/checklists/quickstart-run.md

---

## Dependencies & Execution Order

### Phase Dependencies

- **Setup (Phase 1)**: no dependencies
- **Foundational (Phase 2)**: depends on Setup; blocks all user stories
- **US1 (Phase 3)**: depends on Foundational only. MVP.
- **US2 (Phase 4)**: depends on Foundational; reuses US1 models (Tool, Agent, Run) and builder, so start after T048–T060
- **US3 (Phase 5)**: depends on Foundational and US1 runtime (T060–T062)
- **US4 (Phase 6)**: depends on Foundational and US1 runs (T062, T065); independent from US2 and US3
- **Polish (Phase 7)**: after the stories in scope are done

### Story completion order

```text
Setup → Foundational → US1 (MVP) ─┬─► US2
                                  ├─► US3
                                  └─► US4
                                        └─► Polish
```

US2, US3 and US4 can proceed in parallel once US1 is done; they touch different files except
`runtime/builder.py` (US2 T086–T087, US3 T104) and `runtime/runner.py` (US4 T120), which must be
edited one task at a time.

### Within Each User Story

- Tests first and failing (constitution, principle I)
- Models → migration → services → middleware/runtime → routes → frontend
- A story is done when all its tests pass and its quickstart scenario succeeds

### Parallel Opportunities

- Setup: T003–T007 in parallel after T001–T002
- Foundational tests T009–T014 in parallel; models T019–T020, T022–T023, T027–T028, T031–T032 in parallel
- Each story: all test tasks marked [P] in parallel; models marked [P] in parallel; middleware T056–T059 in parallel; frontend pages marked [P] in parallel

---

## Parallel Example: User Story 1

```bash
# Tests first, together:
Task: "T038 builder unit tests in backend/tests/unit/test_builder.py"
Task: "T039 allowlist middleware tests in backend/tests/unit/test_allowlist_middleware.py"
Task: "T040 budget middleware tests in backend/tests/unit/test_budget_middleware.py"
Task: "T043 run lifecycle integration test in backend/tests/integration/test_run_lifecycle.py"

# Then models together:
Task: "T048 Tool/ToolOperation in backend/src/harness/db/models/catalog.py"
Task: "T049 Agent in backend/src/harness/db/models/agents.py"
Task: "T050 Run/TraceEvent/ApprovalRequest in backend/src/harness/db/models/runs.py"

# Then middleware together:
Task: "T056 trace", "T057 allowlist", "T058 budget", "T059 untrusted-content"
```

## Parallel Example: User Story 2

```bash
Task: "T073 hook rules tests", "T074 webhook tests", "T076 credentials secrecy", "T077 skills"
Task: "T083 skills service", "T084 rule engine", "T085 webhook client"
```

---

## Implementation Strategy

### MVP First (User Story 1 Only)

1. Phase 1 Setup, Phase 2 Foundational (one group created with `harness-admin`)
2. Phase 3 US1
3. **STOP and VALIDATE**: quickstart S1, S2 and S6
4. Demo: launch, live trace, stop, limits, refusal, approval, restart

### Incremental Delivery

1. Foundation → US1 (MVP) → validate → PR
2. US2 catalog and hooks → quickstart S3 → PR
3. US3 memory + eval report → quickstart S4 → PR
4. US4 multi-group admin and service accounts → quickstart S5 → PR
5. Polish → full quickstart run

Each increment is merged through a pull request to `main` (constitution, Workflow) and keeps all
earlier stories passing.

---

## Notes

- [P] tasks = different files, no dependencies on incomplete tasks
- [Story] label maps each task to its user story for traceability
- Any change to an agent prompt, model or agent logic needs the before/after eval report: PRs of US2, US3 and US4 that touch `backend/src/harness/runtime/` or `backend/src/harness/memory/` attach the `core` report against core-baseline.md (T071), and US3 adds the `memory` report (T109)
- Commit after each task or logical group; never mix refactoring and behaviour changes

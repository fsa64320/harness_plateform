# Data Model: Plateforme de harnais d'agents

**Date**: 2026-10-21 | **Spec**: [spec.md](spec.md) | **Plan**: [plan.md](plan.md)

Conventions : toutes les tables métier ont `id` (UUID), `created_at` et `updated_at`. Toute table
rattachée à un groupe porte `group_id` (non nul) et est protégée par une politique Row-Level
Security `group_id = current_setting('app.group_id')` (FR-022, SC-009). La mémoire et les skills
sont des tables métier comme les autres ; seuls les checkpoints LangGraph vivent dans le schéma
`langgraph`, chaque `thread_id` étant rattaché à un `Run` (research R-06, R-07).

## Identités et groupes

### Person
Personne authentifiée par le SSO.
- `oidc_subject` (unique), `email`, `display_name`, `is_platform_admin` (bool)
- Règle : `is_platform_admin` ne donne accès à aucun contenu de groupe (FR-022a).

### Group
- `name` (unique), `status` : `active` | `disabled`
- Réglages : `trace_retention_days` (90), `approval_timeout_hours` (24),
  `proposal_expiry_days` (30), `cost_per_1k_tokens` (0)
- Transition `active → disabled` : arrête les exécutions du groupe et bloque tout accès ; les
  données sont conservées (cas limite de la spec).

### Membership
Appartenance d'une personne à un groupe (FR-022b).
- `group_id`, `person_id` (unique ensemble), `roles` : ensemble parmi `admin`, `tool_manager`,
  `group_memory_manager`, `approver`, `member` (FR-020, cumul permis)
- Quotas : `max_concurrent_runs`, `max_tokens_per_day` (FR-023)
- `status` : `active` | `removed` ; le passage à `removed` arrête les exécutions en cours de la
  personne dans ce groupe.

### ServiceAccount
Identité non humaine d'un groupe (FR-002a).
- `group_id`, `name`, `token_hash`, `token_prefix` (affichage), `status` : `active` | `revoked`
- `allowed_agent_ids` (liste), quotas identiques à `Membership`
- Règles : jamais approbateur ; la révocation arrête ses exécutions et refuse tout lancement.

### AdminAuditEntry
Journal des actions d'administration (FR-024).
- `group_id` (nul pour une action de plateforme), `actor_id`, `action`, `target_type`,
  `target_id`, `details` (JSON sans secret), `occurred_at`

## Catalogue d'outils

### Tool
Élément du catalogue (FR-010).
- `group_id`, `kind` : `mcp_server` | `skill` | `hook`, `name` (unique par groupe et type),
  `description`, `status` : `active` | `retired`, `created_by`
- `mcp_server` : `url` (HTTPS, transport distant uniquement), `credential_id` (optionnel),
  `last_check_at`, `last_check_ok`
- `skill` : `skill_name`, `skill_description` (frontmatter), `files` (JSON chemin → contenu, servi
  en lecture seule par `SqlBackend`)
- `hook` : voir `HookDefinition`
- Règle : un outil `retired` n'est plus lié à aucune exécution ; les agents qui l'utilisaient
  sont signalés « configuration modifiée » (US2-4).

### ToolOperation
Outil élémentaire exposé par un serveur MCP, avec sa classification (FR-005a).
- `tool_id` (serveur MCP), `operation_name`, `input_schema` (JSON),
  `classification` : `read_only` | `external_effect` | `unclassified` (traité comme
  `external_effect`)
- Pour une skill, la classification porte sur la skill elle-même (`read_only` par défaut, car
  aucune exécution de script).

### Credential
Identifiant d'accès chiffré (FR-014).
- `group_id`, `ciphertext`, `nonce`, `key_version`
- Règle : jamais sérialisé dans une réponse d'API, un log ou une trace.

### HookDefinition
Extension d'un `Tool` de type `hook` (FR-012, FR-012a).
- `events` : sous-ensemble de `run_start`, `run_end`, `before_tool_call`, `after_tool_call`
- `hook_type` : `rule` | `webhook`
- `rule` : `condition` (JSON : agent, outil, classification, déclencheur, motif sur paramètres),
  `decision` : `allow` | `block` | `log`
- `webhook` : `url` (HTTPS), `signing_secret_id` (→ `Credential`), `timeout_seconds` (10)
- `priority` (ordre d'évaluation) ; une décision `block` l'emporte sur `allow`.

## Agents et exécutions

### Agent
Configuration réutilisable (FR-001).
- `group_id`, `name`, `instructions`, `model_id` (identifiant Ollama), `created_by`
- `allowed_tool_ids` (MCP, skills, hooks), `agent_memory_enabled`, `group_memory_enabled`,
  `subagents_enabled` (faux par défaut, FR-006a)
- Limites : `max_iterations`, `max_duration_seconds`, `max_tokens`
- `config_changed_at` : renseigné quand un outil utilisé est retiré
- Droits (FR-001a) : tout membre consulte, lance et duplique ; seuls `created_by` et les membres
  `admin` modifient ou suppriment l'agent et sa mémoire d'agent (FR-016).
- Duplication : copie de la configuration, **mémoire d'agent vide**, accès à la mémoire de groupe
  conservé.

### Run
Exécution d'un agent (FR-002, FR-003).
- `group_id`, `agent_id`, `agent_snapshot` (configuration figée au lancement), `task` (texte)
- `triggered_by_type` : `person` | `service_account`, `triggered_by_id`,
  `launch_mode` : `manual` | `programmatic`
- `status` et motif d'arrêt (`stop_reason`) : voir transitions
- `thread_id` (LangGraph), `relaunched_from` (Run, optionnel)
- Consommation : `iterations`, `input_tokens`, `output_tokens`, `cost`, `started_at`, `ended_at`
- `result` (texte final, optionnel)

**Transitions de `status`** :

```text
queued ──► running ──► succeeded
             │  ├────► failed            (erreur explicite)
             │  ├────► stopped           (arrêt manuel)            stop_reason = manual
             │  ├────► limit_reached     (itérations/durée/tokens) stop_reason = <limite>
             │  ├────► interrupted       (redémarrage plateforme, FR-003a)
             │  └────► awaiting_approval ──► running   (décision approve/reject)
             │                          ├──► expired  (délai d'approbation dépassé)
             │                          └──► stopped  (arrêt manuel)
queued ──► rejected_quota                (quota dépassé, FR-023)
```

Règle : `interrupted` est terminal ; seule une relance manuelle crée un nouveau `Run`.

### TraceEvent
Événement ordonné d'une exécution (FR-007). Schéma de diffusion :
[contracts/trace-events.md](contracts/trace-events.md).
- `run_id`, `group_id`, `seq` (entier croissant, unique par run), `occurred_at`, `type`,
  `payload` (JSON **après masquage**, FR-009)
- Index : (`group_id`, `run_id`, `seq`) ; filtres FR-008 via `Run` (agent, déclencheur, statut,
  période)
- Rétention : purge au-delà de `Group.trace_retention_days`.

### ApprovalRequest
Demande d'approbation (FR-005, FR-005b).
- `group_id`, `run_id`, `tool_operation`, `arguments` (masqués), `requested_at`, `expires_at`
- `status` : `pending` → `approved` | `rejected` | `expired`
- `no_eligible_approver` (bool) : vrai quand aucune personne habilitée n'existe (exécution d'un
  compte de service dans un groupe sans `approver`) ; la demande est alors visible en lecture
  par les administrateurs du groupe, qui sont avertis
- `decided_by` (Person), `decided_at`
- Règle d'autorisation : `decided_by` = déclencheur du run (s'il est une personne) **ou** membre
  ayant le rôle `approver` ; jamais un `ServiceAccount`.

## Mémoire

### MemoryEntry
Source de vérité de la mémoire, exposée à l'agent comme des fichiers par `SqlBackend`
(FR-015, FR-016, research R-06).
- `group_id`, `scope` : `agent` | `group`, `agent_id` (si `scope = agent`), `path`
- `content`, `version` (verrou optimiste), `source_run_id`, `author_type` :
  `agent` | `person`, `author_id`
- Unicité : (`group_id`, `scope`, `agent_id`, `path`)

### MemoryRevision
Historique (FR-016).
- `memory_entry_id`, `version`, `previous_content`, `changed_by`, `changed_at`,
  `change_type` : `create` | `update` | `delete` | `conflict`

### GroupMemoryProposal
Proposition d'un agent pour la mémoire de groupe (FR-017a).
- `group_id`, `run_id`, `agent_id`, `proposed_path`, `proposed_content`
- `status` : `pending` → `accepted` | `rejected` | `expired` (après
  `Group.proposal_expiry_days`)
- `decided_by` (rôle `group_memory_manager`), `decided_at`
- Règle : seul `accepted` écrit dans la mémoire de groupe, en créant ou en versionnant un
  `MemoryEntry` de `scope = group`.

## Relations

```text
Person 1─* Membership *─1 Group 1─* ServiceAccount
Group 1─* Tool 1─* ToolOperation        Tool *─0..1 Credential
Group 1─* Agent *─* Tool (allowed_tool_ids)
Agent 1─* Run 1─* TraceEvent            Run 1─* ApprovalRequest
Run 0..1─* Run (relaunched_from)        Run 1─* GroupMemoryProposal
Agent 1─* MemoryEntry (scope=agent)     Group 1─* MemoryEntry (scope=group)
MemoryEntry 1─* MemoryRevision          Group 1─* AdminAuditEntry
```

# Contrat : événements de trace

Route : `GET /api/v1/groups/{groupId}/runs/{runId}/events`

- `Accept: application/json` : liste ordonnée par `seq`, paginée par `after_seq`.
- `Accept: text/event-stream` : flux SSE. Chaque message porte `id: <seq>`, `event: <type>` et
  `data: <TraceEvent JSON>`. Un client qui se reconnecte envoie `Last-Event-ID` et reçoit tous
  les événements suivants, sans perte ni doublon. Le flux se ferme après l'événement `run_ended`.
- Délai cible entre l'occurrence et la diffusion : < 2 s (SC-002).
- Toute charge utile est **masquée** avant écriture (FR-009) : secrets connus du groupe, motifs de
  jetons et de clés, adresses e-mail et numéros de téléphone remplacés par `«masqué»`.
- Chaque champ texte est masqué puis tronqué à 64 Ko ; en cas de troncature, l'objet porte
  `truncated: true` et le `sha256` du contenu complet.
- Reconstitution : `run_started.system_prompt` suivi de la concaténation des `input_messages`
  et des `content` des étapes précédentes redonne exactement le contexte envoyé au modèle à chaque
  étape (constitution IV : trace inspectable et rejouable).

## Enveloppe commune

```json
{
  "run_id": "uuid",
  "seq": 42,
  "type": "tool_call_started",
  "occurred_at": "2026-10-21T12:00:00.000Z",
  "payload": {}
}
```

## Types d'événements

| `type` | Payload (champs principaux) | Exigences |
|---|---|---|
| `run_started` | `agent_id`, `agent_snapshot_version`, `task`, `limits`, `triggered_by_type`, `launch_mode`, `relaunched_from`, `system_prompt` (prompt système effectif, après ajout par le framework des skills et de la mémoire chargée), `internal_tools` (liste effective, FR-006a) | FR-002, FR-006a, FR-007 |
| `model_step` | `iteration`, `input_messages` (messages nouveaux depuis l'étape précédente : tâche, résultats d'outils, contenu de mémoire ou de skill injecté), `content` (texte de raisonnement ou de réponse), `input_tokens`, `output_tokens`, `duration_ms` | FR-007, constitution IV |
| `tool_call_started` | `call_id`, `tool`, `operation`, `classification`, `arguments` | FR-007 |
| `tool_call_finished` | `call_id`, `status` (`ok` / `error`), `result` (tronqué), `duration_ms`, `error` | FR-007, cas limite MCP injoignable |
| `tool_call_refused` | `call_id`, `tool`, `reason` (`not_allowed` / `unknown_tool` / `cross_group`) | FR-006 |
| `hook_evaluated` | `hook_id`, `hook_type`, `event`, `decision` (`allow` / `block` / `log`), `reason`, `duration_ms` | FR-012, FR-013 |
| `approval_requested` | `approval_id`, `call_id`, `operation`, `expires_at` | FR-005, FR-005b |
| `approval_decided` | `approval_id`, `decision` (`approve` / `reject` / `expired`), `decided_by` | FR-005b |
| `memory_read` | `scope` (`agent` / `group`), `path`, `version` | FR-015, FR-017b |
| `memory_written` | `scope` (`agent`), `path`, `version`, `change_type` | FR-015, FR-016 |
| `memory_conflict` | `path`, `expected_version`, `actual_version` | cas limite écriture concurrente |
| `group_memory_proposed` | `proposal_id`, `path` | FR-017a |
| `subagent_started` / `subagent_finished` | `subagent`, `task` / `status` | FR-007 |
| `limit_reached` | `limit` (`iterations` / `duration` / `tokens`), `value`, `max` | FR-003 |
| `config_warning` | `message` (outil retiré depuis la dernière configuration) | US2-4 |
| `run_ended` | `status` (statut final), `stop_reason`, `iterations`, `input_tokens`, `output_tokens`, `cost`, `duration_ms`, `result` | FR-007 |

## Garanties

- `seq` est strictement croissant et sans trou pour un `run_id`.
- Une exécution `interrupted` (FR-003a) a toujours un `run_ended` écrit au redémarrage, avec
  `stop_reason = platform_restart`.
- Aucun champ ne contient d'identifiant d'accès d'outil (FR-014, SC-005).

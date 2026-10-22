# Validation de quickstart.md sur docker compose

- Date : 2026-10-22T03:12:35+02:00
- Pile : `deploy/docker-compose.yml` (postgres, dex, backend, frontend) + `deploy/e2e/docker-compose.e2e.yml`
- Modèle : Ollama `qwen3.5:latest` (hôte, via host.docker.internal)
- Résultat : **33/33 vérifications réussies**

## S1

| Vérification | Résultat | Détail |
|---|---|---|
| run_started, model_step, tool_call_*, run_ended présents | ✅ | ['run_started', 'model_step', 'tool_call_started', 'tool_call_finished', 'model_step', 'run_ended'] |
| exécution « succeeded » | ✅ | succeeded |
| chaque événement arrive sur le flux SSE en moins de 2 s (SC-002) | ✅ | max 0.01s sur 6 événements |
| l'outil MCP a été appelé | ✅ |  |
| arrêt manuel en moins de 5 s (SC-003) | ✅ | 0.54s, statut stopped |
| run_ended porte stop_reason = manual | ✅ |  |
| limite d'itérations : limit_reached | ✅ | limit_reached |

## S2

| Vérification | Résultat | Détail |
|---|---|---|
| send_message jamais exécuté pour un agent qui n'y a pas droit | ✅ |  |
| l'exécution passe en awaiting_approval | ✅ | awaiting_approval |
| Bob (simple membre) ne peut pas décider : 403 | ✅ | 403 |
| après l'approbation d'Alice, l'appel s'exécute | ✅ | statut succeeded |
| approval_decided porte l'identité d'Alice | ✅ |  |

## S3

| Vérification | Résultat | Détail |
|---|---|---|
| le hook règle bloque l'appel à effet externe (aucune approbation demandée) | ✅ | succeeded |
| hook_evaluated désigne le hook et la décision block | ✅ | [{'event': 'before_tool_call', 'reason': 'règle « bloque-effets-externes » : block', 'hook_id': 'b231d5a1-bc27-4076-b5d5-b2bb066c3ce3', 'decision': 'block', 'ho |
| webhook muet : blocage après le délai, cause « timeout » tracée | ✅ | hook « validation-externe » en échec (timeout) |
| l'identifiant MCP a bien atteint le serveur MCP | ✅ |  |
| valeur absente de toutes les réponses d'API et événements de trace | ✅ |  |
| valeur absente des logs du backend | ✅ |  |
| valeur chiffrée au repos (absente de la base) | ✅ |  |

## S4

| Vérification | Résultat | Détail |
|---|---|---|
| memory_written à la première exécution | ✅ | [{'path': 'Acme-preferences.md', 'scope': 'agent', 'version': 1, 'change_type': 'create'}] |
| memory_read (scope = agent) à la seconde exécution | ✅ | [{'path': 'Acme-preferences.md', 'scope': 'agent', 'version': 1}] |
| la réponse réutilise la préférence | ✅ | Par e-mail. |
| group_memory_proposed ; rien dans la mémoire de groupe avant acceptation | ✅ | 1 proposition(s), 0 entrée(s) |
| après acceptation par Carol, un autre agent lit l'entrée (scope = group) | ✅ | [{'path': 'group-memory/faits/tarifs.md', 'scope': 'group', 'version': 1}] |
| agent dupliqué : mémoire d'agent vide | ✅ |  |

## S5

| Vérification | Résultat | Détail |
|---|---|---|
| Dan (autre groupe) : 404 sur agent, trace et mémoire de « demo » | ✅ | [404, 404, 404, 404, 404, 404] |
| lancement par jeton : launch_mode = programmatic | ✅ | programmatic |
| le jeton ne peut pas approuver : 403 | ✅ | 403 |
| révocation : nouveau lancement refusé (401) | ✅ | 204/401 |

## S6

| Vérification | Résultat | Détail |
|---|---|---|
| l'exécution en cours devient « interrompue » (platform_restart) | ✅ | interrupted / platform_restart |
| l'exécution en attente d'approbation est conservée | ✅ | awaiting_approval |
| elle reprend après la décision et exécute l'appel | ✅ | succeeded |
| « Relancer » crée une exécution liée (relaunched_from) | ✅ |  |

## Notes

- Avec un vrai modèle, l'outil non autorisé n'est même pas proposé au modèle (il ne peut donc pas l'appeler) : 0 refus consigné ici. Le refus consigné `not_allowed` / `unknown_tool` est vérifié par les tests automatisés (faux modèle).

## Évaluation de la mémoire (SC-006)

Commande : `cd backend && uv run python -m evals.run --suite memory --model qwen3.5:latest`, contre le même Ollama.
Rapport versionné : `backend/evals/reports/memory.md` (avec mémoire 100 %, sans 20 %, soit +80 points ≥ +20).
Trois exécutions consécutives avec la version finale du prompt : 100/0, 80/20, 100/20 (avec/sans mémoire).
L'état avant le chargement de la mémoire dans le prompt est conservé dans `memory-before-prompt-change.md`
(20 % avec mémoire : le modèle ne consultait pas ses fichiers).

## Déviations par rapport à quickstart.md

- Le modèle est `qwen3.5` servi par l'Ollama de l'hôte (`OLLAMA_BASE_URL=http://host.docker.internal:11434`), pas
  `qwen3` dans le conteneur `ollama` du compose, non démarré.
- La connexion SSO passe par Dex avec ses utilisateurs de test (script) ; `COOKIE_SECURE=false` (http local).
- Le serveur MCP et le service de webhook de test tournent sur l'hôte en HTTPS (certificats auto-signés, approuvés
  par le backend via `SSL_CERT_FILE`, voir `deploy/e2e/docker-compose.e2e.yml`).
- Les mesures de latence SSE (SC-002) comparent l'horodatage du serveur à la réception sur la même machine.
- Le refus consigné d'un outil non autorisé (S2.2) n'est pas déclenché par un vrai modèle : l'outil n'est pas lié
  au modèle, qui ne peut donc pas l'appeler. Le refus `not_allowed` / `unknown_tool` est couvert par les tests
  automatisés avec un faux modèle (`test_allowlist_middleware.py`, `test_run_lifecycle.py`).
- Un modèle réel reste non déterministe : une première passe avait échoué sur S4 (le modèle n'avait pas relu sa
  mémoire) ; le chargement du contenu de la mémoire dans le prompt (entrées encadrées comme données non fiables,
  tracées par `memory_read`) a corrigé ce point, et une pile neuve a ensuite passé les 29 vérifications.

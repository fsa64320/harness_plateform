# Quickstart : valider la plateforme de bout en bout

**Spec**: [spec.md](spec.md) | **Contrats**: [contracts/](contracts/) | **Données**: [data-model.md](data-model.md)

Ce guide sert à prouver que la fonctionnalité marche. Il suppose que l'implémentation
(`/speckit-implement`) a produit `backend/`, `frontend/` et `deploy/` conformes au [plan](plan.md).

## Prérequis

- Docker et Docker Compose
- Un modèle Ollama compatible avec l'appel d'outils (par exemple `qwen3`) ; GPU recommandé
- Un fournisseur OIDC de test (le compose de développement fournit un IdP local)
- `uv` (Python 3.12) et Node 22 pour lancer les tests hors conteneur

## Démarrage

```bash
cp deploy/.env.example deploy/.env    # clé de chiffrement, OIDC, LANGSMITH_TRACING=false
docker compose -f deploy/docker-compose.yml up -d
docker compose -f deploy/docker-compose.yml exec ollama ollama pull qwen3
docker compose -f deploy/docker-compose.yml exec backend harness-admin create-group demo --admin alice@example.test
```

Résultat attendu : l'interface répond sur `http://localhost:5173` et l'API sur
`http://localhost:8000/api/v1/me` (401 sans session).

## Tests automatisés (sans modèle réel)

```bash
cd backend && uv run pytest              # unitaires, intégration (PostgreSQL testcontainers), contrats
cd frontend && npm test
```

Résultat attendu : tout passe ; aucun test n'appelle Ollama (principe I).

## Scénarios de validation

Chaque scénario renvoie aux critères d'acceptation de la spec. Les routes sont décrites dans
[openapi.yaml](contracts/openapi.yaml), les événements dans [trace-events.md](contracts/trace-events.md).

### S1 : lancer, suivre et arrêter un agent (US1, SC-001 à SC-003)

1. Connecté en tant qu'Alice, créer un agent avec un seul outil « lecture seule » et des limites
   basses (5 itérations).
2. Le lancer sur une tâche simple.
3. Attendu : les événements `run_started`, `model_step`, `tool_call_*` apparaissent en moins de
   2 s ; `run_ended` avec `succeeded`.
4. Relancer sur une tâche longue, puis cliquer « Arrêter » : `run_ended` avec `stopped` en moins de
   5 s.
5. Relancer avec 1 itération maximum : `limit_reached` puis `run_ended` avec `limit_reached`.

### S2 : outil non autorisé et approbation (US1-4, US1-5, FR-005b, SC-004)

1. Enregistrer un serveur MCP de test exposant `lookup` (classé `read_only`) et `send_message`
   (laissé non classé).
2. Créer un agent autorisé sur `lookup` seulement ; lui demander d'envoyer un message.
   Attendu : `tool_call_refused` avec `not_allowed`.
3. Autoriser aussi `send_message` ; relancer. Attendu : `approval_requested`, exécution en
   `awaiting_approval`.
4. Bob, simple membre sans rôle d'approbateur, tente de décider : 403. Alice approuve : l'appel
   s'exécute, `approval_decided` porte son identité.

### S3 : catalogue, hooks et identifiants (US2, FR-012a, FR-013, SC-005)

1. Ajouter un hook règle `before_tool_call` qui bloque la classification `external_effect`.
   Attendu : l'appel est bloqué, `hook_evaluated` indique le hook.
2. Remplacer par un hook webhook dont le service ne répond pas. Attendu : blocage après 10 s,
   cause « timeout » tracée.
3. Enregistrer un serveur MCP avec un identifiant. Attendu : aucune réponse d'API, aucun log et
   aucun événement de trace ne contient la valeur de l'identifiant (recherche dans les logs).

### S4 : mémoire d'agent et mémoire de groupe (US3, FR-017a, SC-010)

1. Lancer un agent qui retient une préférence ; la relancer sur une tâche liée. Attendu :
   `memory_written` puis, à la seconde exécution, `memory_read` avec `scope = agent`.
2. L'agent propose un fait commun. Attendu : `group_memory_proposed` ; rien dans la mémoire de
   groupe avant que Carol (gestionnaire de la mémoire de groupe) n'accepte.
3. Après acceptation, un autre agent du groupe avec mémoire de groupe activée lit l'entrée
   (`memory_read`, `scope = group`).
4. Dupliquer le premier agent. Attendu : mémoire d'agent vide.

### S5 : isolation entre groupes et comptes de service (US4, FR-002a, SC-009)

1. Créer un second groupe `autre` avec Dan ; Dan tente de lire un agent, une trace et une mémoire
   de `demo` par leurs identifiants. Attendu : 404 partout.
2. Dans `demo`, créer un compte de service autorisé sur un agent ; lancer une exécution par
   l'API avec son jeton. Attendu : `launch_mode = programmatic` ; s'il atteint un outil à effet
   externe, seul un approbateur peut décider ; le jeton ne peut pas approuver (403).
3. Révoquer le compte de service. Attendu : ses exécutions s'arrêtent et un nouveau lancement est
   refusé (401).

### S6 : redémarrage de la plateforme (FR-003a, FR-003b, SC-011)

1. Lancer une exécution longue et une exécution en attente d'approbation.
2. `docker compose -f deploy/docker-compose.yml restart backend`
3. Attendu : la première passe en `interrupted` avec `run_ended` (`platform_restart`) ; la seconde
   reste en `awaiting_approval` et reprend après la décision.
4. « Relancer » la première : nouvelle exécution avec `relaunched_from` renseigné.

## Évaluations (principe V, SC-006)

```bash
cd backend && uv run python -m evals.run --suite memory --model qwen3
```

Résultat attendu : un rapport compare le taux de réussite avec et sans mémoire sur le même jeu
de tâches ; il est joint à toute PR qui modifie un prompt, un modèle ou la logique d'agent.

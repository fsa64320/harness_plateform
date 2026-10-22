# Harness Plateform

Plateforme multi-groupes où des membres définissent, lancent et suivent des **agents** qui utilisent des
serveurs MCP, des skills et des hooks, avec une mémoire d'agent et une mémoire de groupe.

- Un backend Python unique (API FastAPI + moteur d'exécution) construit chaque agent avec
  [Deep Agents](https://github.com/langchain-ai/deepagents) (LangChain / LangGraph) et un modèle **local servi
  par Ollama** : aucune donnée n'est envoyée à un service tiers par défaut.
- Les points de contrôle (outils autorisés, approbation humaine, hooks, budgets, trace) sont des
  middlewares ; l'état des exécutions et la mémoire vivent dans PostgreSQL, isolés par groupe
  (Row-Level Security).
- Une SPA React suit les exécutions en temps réel (SSE).

La spécification, le plan, le modèle de données, les contrats et le guide de validation sont dans
[`specs/001-agent-harness-platform/`](specs/001-agent-harness-platform/) ; les principes du projet dans
[`.specify/memory/constitution.md`](.specify/memory/constitution.md).

## Architecture

```text
backend/                 Python 3.12, uv
  src/harness/
    api/                 routes REST par domaine (contrat : specs/.../contracts/openapi.yaml)
    auth/                SSO OIDC, comptes de service, rôles et permissions
    groups/              administration des groupes et des membres, journal d'administration
    catalog/             serveurs MCP, skills, hooks (règles et webhooks), identifiants chiffrés
    agents/              définitions d'agents (création, duplication, configuration figée)
    runtime/             builder Deep Agents, middlewares, lanceur, approbations, modèles
    memory/              mémoire d'agent et de groupe (backend de fichiers SQL), propositions
    traces/              écriture de la trace, bus SSE, masquage, rétention
    db/                  modèles SQLAlchemy, sessions, politiques RLS
  alembic/               migrations (dont les politiques RLS)
  evals/                 évaluations d'agents versionnées (constitution V), mesure de charge Ollama
  tests/                 unit, integration (PostgreSQL réel), contract (openapi.yaml)
frontend/                React 18 + Vite + TypeScript strict, client typé généré depuis openapi.yaml
deploy/                  docker-compose.yml, Dockerfiles, IdP de test (Dex), validation de bout en bout
```

Points de sécurité structurants : liste blanche d'outils appliquée deux fois (liaison au modèle et
exécution), approbation humaine des outils « à effet externe » ou non classés, identifiants d'outils
chiffrés (AES-GCM) et jamais renvoyés, résultats d'outils et mémoire traités comme des données non
fiables, MCP distant en HTTPS uniquement (pas de stdio), aucune exécution de script de skill ni de code de
membre, politique réseau contre la SSRF, trace et logs masqués. Revue détaillée :
[`checklists/security-review.md`](specs/001-agent-harness-platform/checklists/security-review.md).

## Démarrage local

Prérequis : Docker et Docker Compose, [Ollama](https://ollama.com) avec un modèle compatible appel d'outils
(par exemple `qwen3`), `uv` (Python 3.12) et Node 22 pour les tests hors conteneur.

```bash
cp deploy/.env.example deploy/.env      # puis renseigner les secrets (voir les commentaires du fichier)
docker compose -f deploy/docker-compose.yml up -d
docker compose -f deploy/docker-compose.yml exec ollama ollama pull qwen3
docker compose -f deploy/docker-compose.yml exec backend harness-admin create-group demo --admin alice@example.test
```

- Interface : <http://localhost:5173> ; API : <http://localhost:8000/api/v1/me> (401 sans session).
- Connexion de test : l'IdP local Dex (utilisateurs `alice`, `bob`, `carol`, `dan` @example.test, mot de passe
  `password`). Son émetteur est `http://dex:5556/dex` : ajouter `127.0.0.1 dex` à `/etc/hosts` pour s'y
  connecter depuis un navigateur. En production, brancher l'IdP de l'organisation (`OIDC_*`).
- Ollama sur l'hôte plutôt que dans Compose : `OLLAMA_BASE_URL=http://host.docker.internal:11434` dans
  `deploy/.env`, et ne pas démarrer le service `ollama`.
- `COOKIE_SECURE=false` n'est admis qu'en développement local en http.
- **Politique réseau** : les serveurs MCP et les webhooks qui ne résolvent pas vers une adresse publique
  sont refusés (anti-SSRF). `HARNESS_ALLOW_PRIVATE_NETWORKS=true` les autorise (développement, réseau privé
  assumé) ; en production, ajouter un filtrage de sortie au niveau du réseau.
- Deux rôles PostgreSQL : le propriétaire exécute les migrations, l'application se connecte avec un rôle sans
  `SUPERUSER` ni `BYPASSRLS` (`harness_app`) ; le backend refuse de démarrer s'il peut contourner la RLS.

## Tests

```bash
cd backend && uv sync && uv run pytest         # unitaires, intégration (PostgreSQL via testcontainers), contrats
cd backend && uv run ruff check . && uv run mypy
cd frontend && npm ci && npm test && npm run lint
```

Aucun test n'appelle Ollama : le modèle est un `FakeProvider` (réponses et appels d'outils scriptés). La
suite d'intégration démarre un PostgreSQL 16 réel (Docker requis), un serveur MCP HTTPS de test et des
services de webhook de test.

## Évaluations (constitution, principe V)

Toute modification d'un prompt, d'un modèle ou de la logique d'agent joint les rapports avant/après.

```bash
cd backend
uv run python -m evals.run --suite core   --model qwen3 --baseline evals/reports/core-baseline.md
uv run python -m evals.run --suite memory --model qwen3          # avec / sans mémoire (SC-006)
uv run python -m evals.run --suite core --provider fake          # rejeu déterministe, sans modèle
uv run python -m evals.load_ollama --model qwen3 --parallel 1 2 4 8 --out evals/reports/load-ollama.md
```

Rapports versionnés : [`core-baseline.md`](backend/evals/reports/core-baseline.md),
[`memory.md`](backend/evals/reports/memory.md) (et `memory-before-prompt-change.md`, l'état avant
l'amélioration du prompt de mémoire), [`load-ollama.md`](backend/evals/reports/load-ollama.md).

**Dimensionnement du modèle (SC-007)** : la plateforme tient 20 exécutions simultanées (test de charge avec
un faux modèle), mais le débit réel dépend du serveur de modèle. Ollama ne traite en parallèle que
`OLLAMA_NUM_PARALLEL` générations (4 dans le compose, 1 par défaut) ; au-delà, les exécutions attendent leur
tour. Mesurer l'hôte cible avec `evals.load_ollama`, puis ajuster ce réglage et la mémoire GPU, ou remplacer
le serveur de modèle (vLLM) derrière l'interface `ModelProvider`.

## Validation de bout en bout

[`specs/001-agent-harness-platform/quickstart.md`](specs/001-agent-harness-platform/quickstart.md) décrit les
scénarios S1 à S6. [`deploy/e2e/run_quickstart.py`](deploy/e2e/run_quickstart.py) les rejoue contre la pile
Compose avec un vrai modèle ; le dernier résultat est dans
[`checklists/quickstart-run.md`](specs/001-agent-harness-platform/checklists/quickstart-run.md).

## Contribuer

Cycle test-first (Rouge → Vert → Refactor), une PR par incrément vers `main`, dépendances épinglées
(`uv.lock`, `package-lock.json`), aucun secret commité. Voir la constitution.

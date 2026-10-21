# Implementation Plan: Plateforme de harnais d'agents

**Branch**: `001-agent-harness-platform` | **Date**: 2026-10-21 | **Spec**: [spec.md](spec.md)

**Input**: Feature specification from `/specs/001-agent-harness-platform/spec.md`

## Summary

Plateforme multi-groupes où des membres définissent, lancent et suivent des agents qui utilisent
des serveurs MCP, des skills et des hooks, avec une mémoire d'agent et une mémoire de groupe.
Approche technique : un backend Python unique (API FastAPI + moteur d'exécution) qui construit
chaque agent avec **Deep Agents** (harnais open source de LangChain, basé sur LangGraph) et un
modèle **local servi par Ollama**. Les points de contrôle exigés par la spec (outils autorisés,
approbation humaine, hooks, budgets, traces) sont des middlewares LangChain branchés sur l'agent ;
l'état des exécutions et la mémoire vivent dans PostgreSQL via le checkpointer et le store
LangGraph. Une SPA React suit les exécutions en temps réel par flux SSE. Le tout est livré en
conteneurs (docker compose en développement). Détails et alternatives : [research.md](research.md).

## Technical Context

**Language/Version**: Python 3.12 (backend) ; TypeScript 5 strict (frontend)

**Primary Dependencies**:
- Exécution d'agents : `deepagents` 0.7.29 (sur `langchain` 1.x / `langgraph`), `langchain-ollama`
  1.1.0, `langchain-mcp-adapters` 0.3.2 (client MCP), `langgraph-checkpoint-postgres` 3.2.0
  (checkpointer + store PostgreSQL)
- API : FastAPI, Pydantic 2, SQLAlchemy 2 (async) + Alembic, `sse-starlette`, Authlib (OIDC),
  `cryptography` (chiffrement des identifiants), `structlog`, `httpx`
- Frontend : React 18, Vite, TanStack Query, React Router
- Toutes les versions sont épinglées dans `uv.lock` et `package-lock.json` (principe II)

**Storage**: PostgreSQL 16 : données métier (SQLAlchemy), checkpoints et store LangGraph
(schéma séparé). Isolation par groupe via `group_id` + Row-Level Security.

**Testing**: pytest, pytest-asyncio, httpx `AsyncClient`, testcontainers (PostgreSQL réel) ;
modèles simulés `GenericFakeChatModel` de `langchain-core` (aucun appel au modèle en test
unitaire) ; vitest + Testing Library côté frontend ; jeux d'évaluation dans `backend/evals/`.

**Target Platform**: Conteneurs Linux (amd64/arm64), cloud-agnostiques ; Ollama sur hôte avec GPU
recommandé.

**Project Type**: Application web (backend API + moteur d'exécution, frontend SPA)

**Performance Goals**: étape visible dans le suivi < 2 s (SC-002) ; arrêt < 5 s dans 99 % des
cas (SC-003) ; 20 exécutions simultanées par groupe (SC-007), le débit du modèle local dépendant
du matériel (voir research.md R-10).

**Constraints**: aucune donnée envoyée à un service tiers par défaut (modèle local, télémétrie
LangSmith désactivée) ; aucun code fourni par un membre exécuté sur la plateforme (FR-012a) ;
modèle Ollama obligatoirement compatible avec l'appel d'outils.

**Scale/Scope**: ~20 groupes × 5–50 membres ; 4 user stories, 34 exigences fonctionnelles.

## Constitution Check

*GATE: Must pass before Phase 0 research. Re-check after Phase 1 design.*

| Principe | Vérification | Statut |
|---|---|---|
| I. Test-first | Modèle simulé injecté via une interface `ModelProvider` ; tests écrits avant chaque middleware, route et service ; PostgreSQL réel en intégration ; tests de non-régression exigés par bug. | ✅ |
| II. Sécurité par défaut | Identifiants chiffrés au repos et jamais renvoyés par l'API ; liste blanche d'outils par agent appliquée deux fois (outils liés + middleware) ; MCP **distant uniquement** (pas de transport stdio, qui lancerait un processus local) ; pas de backend sandbox donc pas d'exécution de scripts de skills ; hooks déclaratifs ou webhooks ; RLS par groupe ; contenus externes encadrés comme données ; dépendances épinglées. | ✅ |
| III. Simplicité / YAGNI | Un seul service backend (API + exécutions en tâches asyncio), une seule base, pas de file de messages ni de cache ; Deep Agents fournit sous-agents, fichiers, mémoire, skills et HITL au lieu de les réécrire. | ✅ (voir Complexity Tracking) |
| IV. Observabilité | Chaque événement d'exécution (étape, appel d'outil, hook, mémoire, approbation, tokens, durée) est écrit dans `trace_events` avec `run_id`, puis diffusé en SSE ; logs JSON `structlog` avec masquage ; traces rejouables. | ✅ |
| V. Agents bornés et évaluables | Limites d'itérations (`recursion_limit`), de durée (timeout) et de tokens/coût (middleware de budget) ; approbation via `interrupt_on` construit depuis la classification du catalogue ; jeux d'évaluation versionnés, résultats avant/après exigés pour tout changement de prompt ou de modèle. | ✅ |
| Contrainte « stack » | Stack fixée par l'amendement 1.1.0 de la constitution, conforme à ce plan. | ✅ |

**Résultat** : gate passée (constitution 1.1.0).

**Re-vérification après Phase 1** : le modèle de données, les contrats et le quickstart ne
remettent en cause aucun principe. Points de vigilance reportés en tâches : tests d'isolation
inter-groupes (SC-009), test de redémarrage (SC-011), tests de masquage des secrets (SC-005).

## Project Structure

### Documentation (this feature)

```text
specs/001-agent-harness-platform/
├── plan.md              # Ce fichier
├── research.md          # Phase 0
├── data-model.md        # Phase 1
├── quickstart.md        # Phase 1
├── contracts/
│   ├── openapi.yaml     # API REST (membres et programmes externes)
│   ├── trace-events.md  # Flux SSE des événements d'exécution
│   └── hook-webhook.md  # Contrat des hooks de type service externe
└── tasks.md             # Phase 2 (/speckit-tasks)
```

### Source Code (repository root)

```text
backend/
├── pyproject.toml
├── alembic/                     # Migrations (dont politiques RLS)
├── src/harness/
│   ├── main.py                  # Application FastAPI
│   ├── config.py                # Configuration (variables d'environnement)
│   ├── db/                      # Session, modèles SQLAlchemy, contexte de groupe RLS
│   ├── auth/                    # OIDC, comptes de service, rôles et permissions
│   ├── groups/                  # Groupes, membres, quotas, journal d'administration
│   ├── catalog/                 # Serveurs MCP, skills, hooks, classification, secrets chiffrés
│   ├── agents/                  # Définitions d'agents (CRUD, duplication)
│   ├── runtime/
│   │   ├── builder.py           # Construit l'agent Deep Agents à partir d'une définition
│   │   ├── models.py            # Interface ModelProvider (Ollama, fake pour les tests)
│   │   ├── middleware/          # allowlist, hooks, budget, trace, redaction
│   │   ├── runner.py            # Lancement, arrêt, reprise, récupération au démarrage
│   │   └── approvals.py         # Demandes d'approbation et reprise (FR-005b, FR-003b)
│   ├── memory/                  # Backends mémoire d'agent / groupe, propositions
│   ├── traces/                  # Écriture, lecture, filtres, flux SSE
│   └── api/                     # Routes REST par domaine
├── tests/
│   ├── unit/
│   ├── integration/             # PostgreSQL via testcontainers
│   └── contract/                # Conformité à contracts/openapi.yaml
└── evals/                       # Jeux d'évaluation versionnés (principe V)

frontend/
├── package.json
├── src/
│   ├── api/                     # Client typé généré depuis openapi.yaml
│   ├── pages/                   # Agents, exécutions, catalogue, mémoire, groupe
│   └── components/              # Trace en direct, demandes d'approbation…
└── tests/

deploy/
├── docker-compose.yml           # postgres, ollama, backend, frontend
└── backend.Dockerfile, frontend.Dockerfile
```

**Structure Decision**: application web en deux projets (`backend/`, `frontend/`) plus `deploy/`.
Le moteur d'exécution reste dans le backend : séparer un service « worker » n'apporte rien tant
que la reprise automatique est exclue (FR-003a) et que 20 exécutions simultanées tiennent dans un
processus asyncio.

## Complexity Tracking

| Violation | Why Needed | Simpler Alternative Rejected Because |
|-----------|------------|-------------------------------------|
| Deux projets (backend + frontend) | SC-001 et l'hypothèse « navigateur » exigent une UI riche avec suivi en temps réel | Le rendu serveur seul rend le suivi en direct et les approbations nettement plus lourds à coder |
| Row-Level Security PostgreSQL en plus des filtres applicatifs | FR-022 / SC-009 : l'isolation entre groupes est une exigence de sécurité majeure | Un simple filtre `WHERE group_id` repose sur chaque requête écrite à la main ; un oubli suffit à fuiter des données |
| Chiffrement applicatif des identifiants d'outils | FR-014 : identifiants jamais visibles après saisie | Un gestionnaire de secrets externe ajoute une dépendance d'infrastructure non imposée par la v1 ; l'interface reste remplaçable |

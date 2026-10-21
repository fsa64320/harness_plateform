# Research: Plateforme de harnais d'agents

**Date**: 2026-10-21 | **Plan**: [plan.md](plan.md)

Choix imposés par le porteur du projet : Python avec un framework d'exécution open source de type
LangChain / Deep Agents, modèle local de type Ollama, conteneurs cloud-agnostiques, React +
TypeScript. Les versions citées sont celles publiées sur PyPI au 2026-10-21.

## R-01 Framework d'exécution des agents

- **Decision**: `deepagents` 0.7.29 (`create_deep_agent`), sur `langchain` 1.x et `langgraph`.
- **Rationale**: Deep Agents fournit d'origine ce que la spec demande : sous-agents à contexte
  isolé, système de fichiers à backends interchangeables, mémoire persistante, skills chargées à
  la demande, approbation humaine des appels d'outils (`interrupt_on`), connexion MCP et gestion
  du contexte (résumé des longs fils). Les points de contrôle propres à la plateforme s'ajoutent
  sous forme de middlewares LangChain, sans forker le framework (principe III).
- **Alternatives considered**: LangGraph seul (il faudrait réécrire fichiers, skills, HITL et
  sous-agents) ; boucle d'agent maison (contraire à YAGNI, beaucoup de code de sécurité à
  maintenir) ; autres frameworks (CrewAI, AutoGen) écartés par le choix explicite de LangChain.
- **Points d'attention**: `deepagents` dépend aussi de `langchain-anthropic`, `langchain-google-genai`
  et `langsmith`. Ils sont installés mais inutilisés ; la télémétrie LangSmith est désactivée
  explicitement (`LANGSMITH_TRACING=false`) pour qu'aucune donnée ne sorte (R-09).

## R-02 Fournisseur de modèles

- **Decision**: Ollama en local via `langchain-ollama` 1.1.0 (`ChatOllama`), derrière une interface
  `ModelProvider` (constitution : fournisseur remplaçable par un double de test). Le modèle est un
  paramètre de l'agent (identifiant Ollama, ex. `qwen3`), validé à l'enregistrement.
- **Rationale**: aucune donnée ne quitte l'infrastructure ; coût marginal nul ; un autre
  fournisseur pourra être ajouté plus tard en implémentant la même interface.
- **Contraintes**: seuls les modèles Ollama qui gèrent l'appel d'outils (tool calling) sont
  acceptés ; la plateforme le vérifie par un appel de test à l'enregistrement du modèle. La qualité
  d'un agent dépend fortement du modèle choisi : les jeux d'évaluation (R-11) servent à le mesurer.
- **Coût**: avec un modèle local, le « budget de coût » (FR-003) est exprimé en tokens et en
  durée ; un coût monétaire n'est calculé que si un administrateur configure un tarif par token
  (0 par défaut).
- **Alternatives considered**: API Claude (meilleure qualité d'outils, mais données envoyées à un
  tiers ; écarté par le choix « modèle local ») ; vLLM (meilleur débit concurrent, plus complexe à
  exploiter ; option de remplacement si SC-007 n'est pas tenu, voir R-10).

## R-03 Construction d'un agent à partir de sa définition

- **Decision**: `runtime/builder.py` transforme une définition d'agent en appel à
  `create_deep_agent` avec :
  - `model` : fourni par `ModelProvider` ;
  - `tools` : **uniquement** les outils autorisés pour l'agent (MCP chargés via
    `MultiServerMCPClient`, plus l'outil interne `propose_group_memory`) ;
  - `skills` : les répertoires de skills autorisées, servis en lecture seule par `SqlBackend` (R-06) ;
  - `memory` / `backend` : `CompositeBackend` (R-06) ;
  - `interrupt_on` : construit depuis la classification du catalogue (R-05) ;
  - `middleware` : allowlist, hooks, budget, trace, masquage (R-04) ;
  - `checkpointer` PostgreSQL (R-07) ; pas de `store` LangGraph.
- **Rationale**: une seule fonction pure et testable relie la configuration à l'exécution ; un
  test unitaire peut vérifier qu'un outil non autorisé n'est jamais lié.
- **Outils internes (FR-006a)**: un `FilesystemMiddleware` explicite avec la liste blanche `ls`,
  `read_file`, `glob`, `grep`, `write_file`, `edit_file`, `delete` (Deep Agents impose
  `read_file`). Le sous-agent généraliste (`task`) n'est ajouté que si `subagents_enabled` vaut
  vrai ; il hérite alors des mêmes outils, middlewares et `interrupt_on` que l'agent parent.
  `write_todos` et `execute` ne sont pas activés (pas de `TodoListMiddleware`, pas de backend
  sandbox). Le middleware allowlist accepte l'union des opérations du catalogue autorisées et
  des outils internes effectifs, listés dans `run_started`.

## R-04 Points de contrôle : middlewares

- **Decision**: middlewares LangChain (`AgentMiddleware`, crochet `wrap_tool_call` et crochets de
  modèle), dans cet ordre :
  1. **trace** : émet un événement pour chaque étape, appel et résultat (FR-007) ;
  2. **allowlist** : refuse et consigne tout appel à un outil hors liste (FR-006), y compris un nom
     d'outil inventé par le modèle ;
  3. **hooks** : évalue les hooks « avant appel » puis « après appel » (FR-012, R-08) ;
  4. **budget** : compte itérations et tokens, arrête l'exécution au dépassement (FR-003) ;
  5. **redaction** : masque secrets et données personnelles dans ce qui est tracé (FR-009) ;
  6. **untrusted-content** : encadre tout résultat d'outil et toute entrée de mémoire comme donnée
     non fiable dans le contexte du modèle (FR-018, principe II).
- **Rationale**: chaque exigence de contrôle est un composant isolé, testable avec un faux modèle.
- **Alternatives considered**: callbacks LangChain (observation seulement, ne peuvent pas bloquer
  un appel) ; contrôles dans chaque outil (dupliqués, faciles à oublier).

## R-05 Approbation humaine

- **Decision**: `interrupt_on` de Deep Agents, construit ainsi : tout outil classé « à effet
  externe » ou non classé → interruption avec décisions `approve` et `reject` ; outils « lecture
  seule » → pas d'interruption (FR-005, FR-005a). Les décisions `edit` et `respond` ne sont pas
  proposées en v1 (simplicité). À l'interruption, le runner crée une `ApprovalRequest`, notifie les
  personnes habilitées (FR-005b) et passe l'exécution en `awaiting_approval`. La décision reprend
  l'exécution par `Command(resume={"decisions": [...]})` sur le même `thread_id`.
- **Rationale**: le mécanisme natif s'appuie sur le checkpointer, donc l'état survit à un
  redémarrage, ce qui donne FR-003b sans code supplémentaire.
- **Délai**: une tâche périodique du runner expire les demandes après 24 h (réglable) et arrête
  l'exécution.

## R-06 Mémoire d'agent et mémoire de groupe

- **Decision**: un backend Deep Agents maison, `SqlBackend`, implémente le protocole de backend
  et lit / écrit dans les tables `MemoryEntry` / `MemoryRevision` (et `Tool.files` pour les
  skills) via la session SQL du groupe, donc **sous Row-Level Security**. Il est la seule source
  de vérité de la mémoire. `CompositeBackend` route :
  - `/memories/` → `SqlBackend`, entrées `scope = agent` de l'agent courant, lecture et écriture
    (FR-017) ;
  - `/group-memory/` → `SqlBackend`, entrées `scope = group`, **lecture seule** pour l'agent ;
  - `/skills/` → `SqlBackend`, fichiers des skills autorisées, lecture seule ;
  - défaut → `StateBackend` : fichiers de travail éphémères de l'exécution.
  L'agent ne peut qu'appeler l'outil `propose_group_memory`, qui crée une
  `GroupMemoryProposal` en attente ; seule la validation par un gestionnaire de la mémoire de
  groupe écrit dans `/group-memory/` (FR-017a). Chaque écriture historise la version précédente
  (FR-016) et émet un événement de trace avec l'origine de l'entrée (FR-017b).
- **Rationale**: réutilise le mécanisme de mémoire de Deep Agents (fichiers chargés via
  `memory`) avec une seule source de vérité ; l'isolation par groupe repose sur la RLS, alors que
  les tables du store LangGraph n'en ont pas. L'écriture dans la mémoire de groupe est impossible
  par construction.
- **Alternatives considered**: `StoreBackend` + `PostgresStore` (deux sources de vérité à
  synchroniser et pas de RLS sur le store).
- **Concurrence**: écriture avec contrôle de version optimiste ; un conflit est consigné et
  visible dans l'historique, aucune écriture n'est perdue (cas limite de la spec).

## R-07 Persistance

- **Decision**: PostgreSQL 16. Données métier, mémoire et skills via SQLAlchemy 2 async + Alembic ;
  seuls les checkpoints (`AsyncPostgresSaver` de `langgraph-checkpoint-postgres` 3.2.0) vivent
  dans un schéma `langgraph` distinct, chaque `thread_id` étant rattaché à un `Run` du groupe ; le
  store LangGraph n'est pas utilisé. Isolation : colonne `group_id` sur toutes les tables métier et
  politiques Row-Level Security activées avec `SET LOCAL app.group_id` à chaque transaction.
- **Rationale**: une seule base pour tout (principe III) ; RLS donne une seconde barrière
  d'isolation (SC-009).
- **Alternatives considered**: base par groupe (exploitation lourde pour ~20 groupes) ; SQLite
  (pas de concurrence ni de RLS).

## R-08 Hooks

- **Decision**: deux types (FR-012a) :
  - **règle déclarative** : condition JSON (agent, outil, catégorie, membre déclencheur, motif sur
    les paramètres) et décision `allow` / `block` / `log`, évaluée en Python pur ;
  - **webhook** : POST HTTPS vers un service enregistré, avec une description minimale de
    l'événement et sans identifiants d'outils (FR-012b), signé HMAC, délai de 10 s, toute erreur
    ou absence de réponse valant `block` (FR-013). Contrat : [contracts/hook-webhook.md](contracts/hook-webhook.md).
  Événements : `run_start`, `run_end`, `before_tool_call`, `after_tool_call`.
- **Rationale**: aucun code de membre n'est exécuté sur la plateforme ; échec sûr par défaut.
- **Alternatives considered**: hooks scripts en bac à sable (écarté par la clarification Q2).

## R-09 Sécurité transverse

- **Authentification**: OIDC (SSO de l'organisation) via Authlib pour les membres ; comptes de
  service authentifiés par jeton opaque, stocké haché (SHA-256 + sel), révocable (FR-002a).
  En développement et en test, l'IdP OIDC est Dex (`ghcr.io/dexidp/dex:v2.41.1`) avec des
  utilisateurs statiques ; en production, l'IdP de l'organisation.
- **Autorisation**: rôles par appartenance à un groupe (administrateur, gestionnaire d'outils,
  gestionnaire de la mémoire de groupe, approbateur, membre), vérifiés dans une dépendance FastAPI
  unique ; l'administrateur de plateforme ne lit pas le contenu des groupes (FR-022a).
- **Identifiants d'outils**: chiffrés AES-GCM avec une clé fournie par variable d'environnement,
  jamais renvoyés par l'API, injectés uniquement dans le client MCP au moment de l'exécution
  (FR-014).
- **MCP**: transports distants seulement (streamable HTTP / SSE), pour que la plateforme ne
  lance aucun processus local ; URL vérifiée à l'enregistrement (FR-011).
- **Skills**: aucune exécution de scripts, faute de backend sandbox ; les skills servent
  d'instructions et de fichiers de référence.
- **Données sortantes**: aucune par défaut (modèle local, LangSmith désactivé) ; seuls les
  webhooks de hooks et les serveurs MCP enregistrés reçoivent des données.

## R-10 Concurrence et performance

- **Decision**: exécutions lancées comme tâches asyncio dans le processus backend, avec un
  sémaphore global et un compteur par groupe (quotas FR-023). Événements de trace écrits en base
  puis diffusés par SSE via un bus en mémoire du processus. Arrêt : annulation de la tâche, avec
  vérification au plus tard entre deux étapes (SC-003).
- **Risque SC-007**: 20 exécutions simultanées imposent 20 générations en parallèle côté Ollama
  (`OLLAMA_NUM_PARALLEL`) et dépendent du GPU disponible. La plateforme elle-même est testée à
  20 exécutions avec un faux modèle ; la capacité réelle du modèle est mesurée sur le matériel
  cible. Si elle est insuffisante, l'option est vLLM derrière la même interface `ModelProvider`.
- **Alternatives considered**: file de messages + workers (Celery, Redis) : inutile tant qu'un
  seul processus suffit et que la reprise automatique est exclue (FR-003a).

## R-11 Tests et évaluations

- **Decision**: TDD avec `GenericFakeChatModel` (réponses et appels d'outils scriptés) pour tous
  les tests du runtime ; serveur MCP de test en processus pour l'intégration ; PostgreSQL réel via
  testcontainers ; tests de contrat sur `openapi.yaml`. Les évaluations (`backend/evals/`) jouent
  des tâches de référence contre le vrai modèle Ollama, hors CI unitaire, et produisent un rapport
  avant/après pour tout changement de prompt, de modèle ou de logique d'agent (principe V,
  SC-006).

## R-12 Interruption et redémarrage

- **Decision**: au démarrage, le runner passe toute exécution `running` à `interrupted` en
  conservant sa trace (FR-003a) ; les exécutions `awaiting_approval` sont conservées et reprennent
  dès la décision (FR-003b, grâce au checkpointer). « Relancer » crée une nouvelle exécution liée
  à l'ancienne (`relaunched_from`), dont la trace reste visible par l'approbateur.

## R-13 Frontend et temps réel

- **Decision**: React 18 + Vite + TanStack Query ; client typé généré depuis `openapi.yaml` ;
  suivi en direct par `EventSource` sur le flux SSE de l'exécution, avec reprise par
  `Last-Event-ID`.
- **Alternatives considered**: WebSocket (bidirectionnel inutile : les commandes passent par REST).

## R-14 Déploiement

- **Decision**: images Docker du backend et du frontend ; `deploy/docker-compose.yml` avec
  `postgres`, `ollama`, `backend` et `frontend` pour le développement et les petites
  installations ; configuration uniquement par variables d'environnement, aucun secret dans les
  images. Kubernetes ou un service managé plus tard, sans changer le code.

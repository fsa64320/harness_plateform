# Revue de sécurité : plateforme de harnais d'agents (T128)

**Périmètre** : tout le diff de la branche `001-agent-harness-platform` (backend, frontend, déploiement).
**Méthode** : lecture du code par surface d'attaque, tests de non-régression pour chaque constat corrigé,
audit des dépendances (`pip-audit` : aucune vulnérabilité connue ; `npm audit --audit-level=high` : 0).

## Contrôles demandés

| Contrôle | Résultat | Preuve |
|---|---|---|
| Secrets absents des logs | ✅ après correction (F-2) | `tests/unit/test_log_redaction.py`, `tests/integration/test_credentials_secrecy.py` (journal capturé), grep des logs du backend dans `quickstart-run.md` (S3) |
| Secrets absents des réponses d'API, des traces, de la base en clair | ✅ | `test_credentials_secrecy.py` : toutes les surfaces d'API, `trace_events`, `runs.result`, journal d'administration, colonne chiffrée |
| RLS sur toutes les tables de groupe | ✅ renforcé (F-6) | `test_rls_isolation.py` : activée, forcée, lignes invisibles et non modifiables d'un autre groupe, insertion refusée, et **toute** table portant `group_id` est protégée (contrôle en base) |
| Isolation des routes entre groupes | ✅ | `test_cross_group_api.py` : routes énumérées depuis OpenAPI, 404 partout (SC-009), y compris avec les identifiants réels d'un autre groupe |
| Appels sortants limités aux serveurs MCP enregistrés et aux webhooks | ✅ après correction (F-1) | `net_policy.py` ; `tests/unit/test_outbound_surface.py` limite les modules qui ouvrent des connexions |
| Aucun MCP stdio | ✅ | `test_outbound_surface.py::test_no_stdio_mcp_transport` ; seul `streamable_http` est configuré |
| Aucune exécution de script ni de code de membre | ✅ | `test_outbound_surface.py` (aucun `subprocess`, `eval`, `exec`, `compile`, `open`) ; `test_skills.py::test_no_script_of_a_skill_is_ever_executed` (l'outil `execute` n'est jamais lié) |
| Outils : liste blanche, approbation, contenu non fiable | ✅ | `test_allowlist_middleware.py`, `test_approvals.py`, `test_untrusted_content.py`, `test_memory_untrusted.py` |
| Comptes de service : jamais approbateurs, jetons salés/hachés, révocables | ✅ | `test_service_accounts.py` |
| Administrateur de plateforme sans accès au contenu | ✅ | `test_permissions.py`, `test_admin_contract.py` |

## Constats et corrections

| ID | Gravité | Constat | Correction | Test de non-régression |
|---|---|---|---|---|
| F-1 | Élevée | **SSRF** : un gestionnaire d'outils pouvait enregistrer une URL HTTPS visant le réseau interne ou les métadonnées du cloud (`169.254.169.254`) ; la plateforme l'appelait depuis son réseau (serveurs MCP et webhooks). | `harness/net_policy.py` : tout hôte qui ne résout pas vers une adresse publique est refusé à l'enregistrement, au lancement d'une exécution et à l'appel d'un webhook ; `HARNESS_ALLOW_PRIVATE_NETWORKS` l'autorise explicitement (développement). | `tests/unit/test_net_policy.py`, `tests/integration/test_outbound_policy.py` |
| F-2 | Moyenne | Les tracebacks des logs passaient dans le masquage **avant** leur mise en forme : un secret contenu dans un message d'exception pouvait être journalisé. Les identifiants étaient aussi inconnus du masquage des logs. | Mise en forme des exceptions avant le masquage ; registre des secrets connus du processus (identifiants chargés ou enregistrés) consulté par le masquage des logs. | `tests/unit/test_log_redaction.py` |
| F-3 | Élevée | Le **résultat final** d'une exécution (sortie du modèle) était stocké et renvoyé sans masquage : un modèle qui recopie un identifiant l'exposait dans `Run.result`. Trouvé par le test de secret de bout en bout. | `result` masqué avec les secrets du groupe avant écriture et diffusion. | `tests/integration/test_credentials_secrecy.py` |
| F-4 | Moyenne | **Détournement d'invitation** : une invitation se rattache à une personne par son e-mail ; un fournisseur d'identité qui renvoie un e-mail non vérifié permettait de se faire passer pour l'invité. | Connexion refusée (403) si `email_verified` vaut `false` ; e-mail normalisé. | `tests/unit/test_security_hardening.py` |
| F-5 | Faible | Entrées de mémoire et propositions de taille illimitée (déni de service par un agent bavard ou malveillant). | Limite de 256 Ko par entrée, appliquée au service, au backend de fichiers, à l'API et à l'outil de proposition. | `tests/unit/test_security_hardening.py` |
| F-6 | Faible | Le test d'isolation ne couvrait que les tables héritant de `GroupScopedMixin` : une table future avec `group_id` mais sans le mixin y échappait. | Contrôle direct en base : toute table portant `group_id` doit avoir la RLS activée et forcée. | `tests/integration/test_rls_isolation.py` |
| F-7 | Faible | Interblocage possible du pool de connexions sous charge : une requête gardait sa connexion pendant que le lanceur en ouvrait une seconde (20 lancements simultanés épuisaient le pool). Trouvé par le test de charge. | L'authentification et l'appartenance utilisent chacune une transaction brève et distincte ; la session de la requête n'est ouverte que par les routes qui accèdent aux données ; pool agrandi. | `tests/integration/test_concurrency.py` |
| F-8 | Faible | Front statique servi sans en-têtes de sécurité. | CSP `default-src 'self'`, `nosniff`, `frame-ancestors 'none'`, `Referrer-Policy` dans `deploy/nginx.conf`. | Configuration (sans logique) |

Conception défensive déjà en place et vérifiée : rôle applicatif sans `BYPASSRLS` et refus de démarrer
sinon, `FORCE ROW LEVEL SECURITY`, comparaison à temps constant des jetons, cookie de session `HttpOnly` /
`SameSite=Lax` / `Secure`, redirections HTTP désactivées pour les webhooks, signature HMAC horodatée des
webhooks, secrets d'actions GitHub épinglés par SHA, conteneurs non-root, aucun secret dans les images.

## Risques résiduels acceptés

- **Rebinding DNS** : la résolution est contrôlée avant chaque usage mais pas verrouillée jusqu'à la
  connexion. Compléter par un filtrage de sortie au niveau du réseau (pare-feu ou proxy sortant) en production.
- **Redirections du client MCP** : le client MCP suit les redirections HTTP ; une redirection d'un serveur
  enregistré vers une adresse interne n'est pas re-contrôlée (l'en-tête d'autorisation n'est pas transmis
  hors origine). Même mitigation réseau.
- **Taille des réponses de webhook** : bornée par le délai de 10 s, pas par un plafond d'octets.
- **Sessions** : cookie signé de 8 h, sans révocation côté serveur (la désactivation d'un membre ou d'un
  groupe est en revanche immédiate, car l'appartenance est relue à chaque requête).
- **Pas de limitation de débit** sur l'API ni de plafond de flux SSE par utilisateur : à ajouter au niveau de
  la passerelle si la plateforme est exposée hors d'un réseau de confiance.
- **Un hook webhook voit les arguments d'appel masqués** (jamais les identifiants ni la mémoire) : c'est
  son rôle ; le choisir et l'autoriser reste une décision du gestionnaire d'outils.
- **Prompt injection** : le contenu externe est encadré comme donnée et ne change ni instructions ni outils,
  mais un modèle peut toujours être influencé dans ses *réponses* ; les actions à effet externe restent
  soumises à l'approbation humaine, qui est la défense finale.

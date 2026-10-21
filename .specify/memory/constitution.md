# Harness Plateform Constitution

## Core Principles

### I. Test-first (NON NÉGOCIABLE)

- Tout comportement nouveau ou modifié DOIT commencer par un test qui échoue, écrit avant le code
  de production (cycle Rouge → Vert → Refactor).
- Un changement sans test qui l'exerce NE DOIT PAS être fusionné, sauf pour la documentation ou
  la configuration sans logique.
- Les comportements d'agents (appels d'outils, enchaînements, gestion d'erreurs) DOIVENT être
  testés de façon déterministe, avec des réponses de modèle simulées ou enregistrées ; les tests
  unitaires NE DOIVENT PAS dépendre d'un appel réseau à un fournisseur de LLM.
- Un bug corrigé DOIT laisser derrière lui un test de non-régression.

Justification : la sortie d'un LLM est non déterministe ; seuls des tests écrits d'abord et
isolés du modèle permettent de savoir si la plateforme, elle, se comporte correctement.

### II. Sécurité par défaut (NON NÉGOCIABLE)

- Aucun secret (clé d'API, jeton, mot de passe) NE DOIT être commité ; ils sont lus depuis
  l'environnement ou un gestionnaire de secrets, et le push protection de GitHub reste actif.
- Chaque agent, outil et intégration DOIT recevoir le moindre privilège nécessaire : liste
  explicite d'outils autorisés, accès fichiers et réseau restreints, jetons à portée minimale.
- Tout contenu externe (pages web, fichiers, sorties d'outils, réponses de modèles) DOIT être
  traité comme une donnée non fiable, jamais comme une instruction ; les défenses contre
  l'injection de prompt font partie du design de chaque fonctionnalité.
- Toute nouvelle dépendance DOIT être justifiée dans la PR, provenir d'une source reconnue et
  être épinglée à une version précise.

Justification : une plateforme qui exécute des agents leur délègue des actions réelles ; une
faille ou un secret exposé y a des conséquences directes.

### III. Simplicité et YAGNI

- La solution la plus simple qui satisfait la spec DOIT être retenue ; aucune abstraction,
  couche ou configuration NE DOIT être ajoutée sans besoin présent et démontré.
- Toute complexité supplémentaire (nouveau service, framework, pattern d'indirection) DOIT être
  justifiée par écrit dans la section « Complexity Tracking » du plan, avec l'alternative plus
  simple écartée et la raison.
- Le code mort, les options inutilisées et les fonctionnalités spéculatives DOIVENT être
  supprimés plutôt que conservés « au cas où ».

Justification : chaque couche ajoutée autour d'un modèle est une surface de bug et de sécurité
de plus ; on n'en paie le coût que lorsqu'il est nécessaire.

### IV. Observabilité des exécutions

- Chaque exécution d'agent DOIT produire une trace structurée et corrélée par un identifiant
  d'exécution : prompts, appels d'outils, résultats, décisions, durées, tokens et coûts.
- Les logs DOIVENT être structurés (JSON ou équivalent) et NE DOIVENT JAMAIS contenir de secrets
  ni de données personnelles non masquées.
- Les erreurs DOIVENT être explicites et remonter avec leur contexte ; aucune erreur NE DOIT
  être avalée silencieusement.
- Une exécution DOIT pouvoir être rejouée ou inspectée a posteriori à partir de sa trace.

Justification : sans trace, le comportement d'un agent est inexplicable et donc non débogable,
non auditable et non améliorable.

### V. Agents bornés et évaluables

- Chaque exécution d'agent DOIT avoir des limites explicites : nombre d'itérations, durée,
  budget de tokens ou de coût ; le dépassement d'une limite arrête l'exécution proprement.
- Toute action irréversible ou à effet externe (envoi, suppression, publication, paiement,
  déploiement) DOIT exiger une confirmation humaine explicite, sauf autorisation préalable
  documentée.
- Toute modification de prompt, de modèle ou de logique d'agent qui change un comportement
  DOIT être accompagnée d'un jeu d'évaluation versionné et de ses résultats avant/après.

Justification : un agent sans bornes ni évaluation peut coûter cher, agir à tort ou régresser
sans que personne ne s'en aperçoive.

## Contraintes de sécurité et d'exécution des agents

- La stack technique n'est pas encore fixée ; son choix DOIT faire l'objet d'un amendement de
  cette constitution, qui précisera langage, gestionnaire de dépendances et outillage de test.
- Les identifiants de modèles DOIVENT être configurables et non codés en dur dans la logique
  métier.
- Les fournisseurs de LLM, serveurs MCP et outils externes DOIVENT être appelés derrière une
  interface remplaçable par un double de test.
- Les données envoyées à un fournisseur externe DOIVENT être limitées au strict nécessaire.

## Workflow de développement

- La branche `main` est protégée : pas de suppression, pas de force-push, et tout changement
  DOIT passer par une pull request.
- Une PR DOIT référencer la spec Spec Kit concernée (`specs/<feature>/`) dès qu'elle touche à un
  comportement, et suivre le cycle `/speckit-specify` → `/speckit-plan` → `/speckit-tasks` →
  `/speckit-implement`.
- Une PR NE DOIT être fusionnée que si les tests passent, que les nouvelles dépendances sont
  justifiées et que la vérification de conformité à cette constitution est faite (section
  « Constitution Check » du plan).
- Les messages de commit DOIVENT décrire le changement de façon explicite ; un commit ne mélange
  pas refactoring et changement de comportement.

## Governance

- Cette constitution prévaut sur toute autre pratique du projet. En cas de conflit entre un plan,
  une spec ou une habitude et la constitution, la constitution s'applique.
- Tout amendement DOIT passer par une PR qui modifie ce fichier, explique la raison du changement
  et décrit l'impact sur les specs et plans existants.
- Versionnage sémantique :
  - MAJOR : suppression ou redéfinition incompatible d'un principe ou d'une règle de gouvernance ;
  - MINOR : ajout d'un principe ou d'une section, ou extension substantielle d'une règle ;
  - PATCH : clarification, reformulation ou correction sans effet sur le sens.
- Chaque plan d'implémentation DOIT vérifier sa conformité aux principes I à V ; tout écart DOIT
  être justifié dans « Complexity Tracking » ou corrigé avant implémentation.
- La conformité est revue à chaque PR et la constitution est relue à chaque changement de stack
  ou d'architecture majeur.

**Version**: 1.0.0 | **Ratified**: 2026-10-21 | **Last Amended**: 2026-10-21

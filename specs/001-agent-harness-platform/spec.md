# Feature Specification: Plateforme de harnais d'agents

**Feature Branch**: `001-agent-harness-platform`

**Created**: 2026-10-21

**Status**: Draft

**Input**: User description: "Un groupe d'utilisateurs veut utiliser une plateforme de harnais pour lancer des agents. Ces agents utilisent des tools (mcp, skills, hooks) et possèdent une mémoire"

## Clarifications

### Session 2026-10-21

- Q: Qui décide qu'une action d'un agent est « irréversible ou à effet externe » et doit attendre
  une approbation humaine ? → A: Chaque outil est classé dans le catalogue par le gestionnaire
  d'outils (« lecture seule » ou « à effet externe ») ; un outil non classé est traité comme « à
  effet externe ».
- Q: Que fait concrètement un hook quand il se déclenche ? → A: Soit une règle déclarative
  (condition et décision : autoriser, bloquer ou journaliser), soit un appel à un service externe
  enregistré qui renvoie la décision ; aucun code n'est exécuté sur la plateforme.
- Q: Comment une exécution d'agent peut-elle être déclenchée dans cette première version ? → A: À
  la main par un membre, et par un programme externe (script, CI) authentifié au nom d'un membre
  ou d'un compte de service du groupe ; ni lancement planifié ni déclenchement par événement.
- Q: Qui a le droit d'approuver ou de refuser un appel à un outil « à effet externe » quand une
  exécution est en pause ? → A: Le membre qui a lancé l'exécution, ou un membre ayant le rôle
  « approbateur » ; pour une exécution lancée par un compte de service, uniquement un approbateur.
- Q: Si la plateforme s'interrompt pendant qu'une exécution tourne, que doit devenir cette
  exécution ? → A: Elle est marquée « interrompue », sa trace est conservée et elle peut être
  relancée de zéro ; une exécution en attente d'approbation est conservée et reprend dès que la
  décision est prise.

## User Scenarios & Testing *(mandatory)*

### User Story 1 - Lancer un agent et suivre son exécution (Priority: P1)

Un membre du groupe définit un agent (nom, instructions, outils autorisés, limites d'exécution),
lui confie une tâche décrite en texte, puis suit son exécution pas à pas : chaque étape de
raisonnement, chaque appel d'outil avec son résultat, la durée et le coût consommés. Il peut
arrêter l'exécution à tout moment et consulter ensuite la trace complète.

**Why this priority**: c'est la raison d'être de la plateforme. Sans lancement ni suivi, aucune
autre fonctionnalité n'a de valeur ; cette story seule constitue déjà un produit utilisable.

**Independent Test**: créer un agent avec un seul outil autorisé, lui donner une tâche simple,
vérifier que la trace affiche chaque étape, que l'arrêt manuel fonctionne et que la trace reste
consultable après la fin.

**Acceptance Scenarios**:

1. **Given** un membre connecté et un agent défini avec des outils autorisés, **When** il lance
   l'agent sur une tâche, **Then** l'exécution démarre et chaque étape apparaît dans la trace au
   fil de l'eau.
2. **Given** une exécution en cours, **When** le membre demande l'arrêt, **Then** l'exécution
   s'interrompt proprement et la trace indique qu'elle a été arrêtée manuellement.
3. **Given** une exécution qui atteint sa limite d'itérations, de durée ou de budget, **When** la
   limite est dépassée, **Then** l'exécution s'arrête et la trace indique quelle limite a été
   atteinte.
4. **Given** un agent qui tente d'utiliser un outil non autorisé, **When** l'appel est demandé,
   **Then** il est refusé et le refus est consigné dans la trace.
5. **Given** un agent qui appelle un outil classé « à effet externe » (ou non classé), **When**
   l'appel est sur le point d'être exécuté, **Then** l'exécution se met en pause jusqu'à ce qu'un
   membre habilité (FR-005b) l'approuve ou la refuse.

---

### User Story 2 - Gérer le catalogue d'outils : MCP, skills et hooks (Priority: P2)

Un membre habilité enregistre des outils dans le catalogue du groupe : des serveurs MCP
(fournisseurs d'outils externes), des skills (procédures réutilisables que l'agent peut charger)
et des hooks (règles ou appels à un service externe déclenchés automatiquement à des moments
précis de l'exécution, par exemple avant un appel d'outil, pour l'autoriser, le bloquer ou le
journaliser). Les membres
choisissent ensuite, pour chaque agent, lesquels de ces outils il peut utiliser.

**Why this priority**: sans catalogue, les agents se limitent à un jeu d'outils fixe. C'est ce qui
rend la plateforme extensible, mais la P1 reste démontrable avec un jeu d'outils de base.

**Independent Test**: enregistrer un serveur MCP, une skill et un hook, les associer à un agent,
lancer cet agent et vérifier dans la trace que l'outil MCP est appelé, que la skill est chargée et
que le hook s'est déclenché au bon moment.

**Acceptance Scenarios**:

1. **Given** un membre habilité, **When** il enregistre un serveur MCP, **Then** la plateforme
   vérifie qu'il est joignable et affiche la liste des outils qu'il expose.
2. **Given** une skill enregistrée, **When** un agent qui y a droit en a besoin, **Then** il la
   charge et son utilisation apparaît dans la trace.
3. **Given** un hook « avant appel d'outil » qui bloque une catégorie d'outils, **When** un agent
   tente un appel de cette catégorie, **Then** l'appel est bloqué et la trace indique quel hook l'a
   bloqué.
4. **Given** un outil retiré du catalogue, **When** un agent qui l'utilisait est lancé, **Then** il
   s'exécute sans cet outil et le membre est averti que sa configuration a changé.
5. **Given** un outil qui nécessite un identifiant d'accès, **When** il est enregistré, **Then**
   l'identifiant est stocké de façon protégée et n'apparaît jamais dans les traces ni à l'écran.

---

### User Story 3 - Donner une mémoire persistante aux agents (Priority: P3)

Un agent retient d'une exécution à l'autre ce qui est utile : préférences, faits établis,
décisions passées. Les membres peuvent consulter, corriger et supprimer le contenu de cette
mémoire, et voir quelles entrées ont été utilisées lors d'une exécution donnée.

**Why this priority**: la mémoire améliore nettement la qualité des exécutions répétées, mais un
agent sans mémoire reste utile ; elle vient donc après le lancement et les outils.

**Independent Test**: lancer un agent qui enregistre une information, le relancer sur une tâche
qui en dépend et vérifier qu'il la réutilise ; supprimer l'entrée et vérifier qu'elle n'est plus
réutilisée.

**Acceptance Scenarios**:

1. **Given** un agent avec mémoire activée, **When** une exécution produit une information à
   retenir, **Then** une entrée de mémoire est créée avec sa source (exécution d'origine, date).
2. **Given** une entrée de mémoire existante, **When** l'agent est relancé sur une tâche liée,
   **Then** l'entrée est réutilisée et la trace indique quelles entrées ont été consultées.
3. **Given** un membre autorisé, **When** il modifie ou supprime une entrée, **Then** la
   modification s'applique dès l'exécution suivante et est historisée.
4. **Given** un contenu externe contenant des instructions, **When** l'agent l'enregistre en
   mémoire, **Then** il est conservé comme une donnée et ne modifie jamais les instructions de
   l'agent.
5. **Given** la portée de mémoire définie pour l'agent, **When** un autre agent ou un autre membre
   s'exécute, **Then** il n'accède qu'aux entrées que cette portée l'autorise à voir (portée
   définie par FR-017).
6. **Given** un agent pour lequel la mémoire de groupe est activée, **When** il juge qu'une
   information est utile à tout le groupe, **Then** il la propose et elle n'entre dans la mémoire
   de groupe qu'après validation par un gestionnaire de la mémoire de groupe.
7. **Given** une entrée validée dans la mémoire de groupe, **When** un autre agent du groupe
   pour lequel elle est activée s'exécute sur une tâche liée, **Then** il peut la réutiliser.

---

### User Story 4 - Travailler en groupe (Priority: P4)

Les membres d'un groupe partagent leurs agents, leurs outils et l'historique des exécutions.
Un administrateur gère les membres et leurs rôles, ce qui détermine qui peut lancer, modifier
ou supprimer un agent, et qui peut gérer le catalogue d'outils.

**Why this priority**: le besoin exprimé concerne un groupe d'utilisateurs, mais les stories
précédentes sont démontrables avec un seul membre ; le partage et les rôles viennent ensuite.

**Independent Test**: avec deux membres de rôles différents, vérifier que l'un voit et relance
l'agent de l'autre selon ses droits, et qu'une action non permise par son rôle est refusée.

**Acceptance Scenarios**:

1. **Given** un administrateur, **When** il invite une personne et lui attribue un rôle,
   **Then** cette personne accède au groupe avec exactement les droits de ce rôle.
2. **Given** un agent créé par un membre, **When** un autre membre du groupe consulte la liste,
   **Then** il voit l'agent et ses exécutions passées.
3. **Given** un membre sans droit de gestion des outils, **When** il tente d'enregistrer un outil,
   **Then** l'action est refusée.
4. **Given** une personne retirée du groupe, **When** elle tente d'y accéder, **Then** l'accès est
   refusé et ses exécutions en cours sont arrêtées.
5. **Given** deux groupes distincts sur la plateforme, **When** un membre du premier recherche des
   agents, outils, mémoires ou traces, **Then** il ne voit rien qui appartienne au second, même
   s'il en connaît l'identifiant.
6. **Given** une personne membre de deux groupes, **When** elle passe de l'un à l'autre, **Then**
   elle n'a dans chacun que les droits du rôle qui lui y est attribué.

---

### Edge Cases

- Que se passe-t-il si un serveur MCP devient injoignable en cours d'exécution ? L'appel échoue
  avec une erreur explicite dans la trace ; l'agent peut poursuivre sans cet outil ou s'arrêter.
- Que se passe-t-il si un hook échoue ou ne répond pas ? L'action concernée est bloquée par
  défaut (échec sûr) et l'erreur est consignée.
- Que se passe-t-il si une exécution reste en attente d'approbation humaine plus longtemps que le
  délai d'attente d'approbation (24 heures par défaut) ? Elle s'arrête et la trace l'indique.
- Que se passe-t-il si une exécution relancée après une interruption rejoue un appel à effet
  externe déjà exécuté avant l'interruption ? Comme toute exécution, elle repasse par
  l'approbation ; la trace de l'exécution interrompue, liée à la nouvelle, montre ce qui avait
  déjà été exécuté pour que l'approbateur puisse refuser un doublon.
- Que se passe-t-il si deux exécutions du même agent modifient la même entrée de mémoire en même
  temps ? Aucune écriture n'est perdue silencieusement ; le conflit est visible dans l'historique.
- Que se passe-t-il si un résultat d'outil contient des instructions destinées à l'agent ? Il est
  traité comme une donnée, jamais comme une instruction.
- Que se passe-t-il si un outil renvoie un secret ou une donnée personnelle ? Il est masqué dans
  la trace affichée.
- Que se passe-t-il si un agent d'un groupe tente d'utiliser un outil, un identifiant d'accès ou
  une mémoire d'un autre groupe ? L'accès est refusé et la tentative est journalisée.
- Que se passe-t-il si un groupe est désactivé pendant que des exécutions tournent ? Elles sont
  arrêtées et plus aucun membre ne peut accéder au groupe ; ses données restent conservées.
- Que se passe-t-il si un agent est dupliqué ? La copie démarre avec une mémoire d'agent vide ;
  la mémoire de l'original n'est jamais partagée avec elle. Elle garde l'accès à la mémoire de
  groupe si celui-ci est activé.
- Que se passe-t-il si une entrée de mémoire de groupe contredit une entrée de mémoire d'agent ?
  Les deux sont présentées à l'agent avec leur origine, et la contradiction apparaît dans la trace.
- Que se passe-t-il si une proposition de mémoire de groupe n'est jamais validée ? Elle expire
  après 30 jours par défaut et n'est jamais utilisée tant qu'elle n'est pas validée.
- Que se passe-t-il si une exécution lancée par un compte de service atteint un outil à effet
  externe ? Elle se met en pause et la demande d'approbation est présentée aux approbateurs du
  groupe ; le compte de service ne peut jamais s'auto-approuver.
- Que se passe-t-il si un groupe n'a aucun approbateur et qu'une exécution lancée par un compte
  de service attend une approbation ? La demande expire au délai d'attente, l'exécution s'arrête
  et les administrateurs du groupe sont avertis qu'aucun approbateur n'est désigné.
- Que se passe-t-il si un compte de service est révoqué pendant que ses exécutions tournent ?
  Elles sont arrêtées et tout nouveau lancement avec ce compte est refusé.
- Que se passe-t-il si un membre lance plus d'exécutions que son quota ? Les exécutions
  supplémentaires sont refusées avec un message explicite.

## Requirements *(mandatory)*

### Functional Requirements

**Agents et exécutions**

- **FR-001**: Les membres DOIVENT pouvoir créer, modifier, dupliquer et supprimer un agent défini
  par un nom, des instructions, une liste d'outils autorisés et des limites d'exécution.
- **FR-002**: Une exécution DOIT pouvoir être lancée sur une tâche décrite en texte libre de deux
  façons : à la main par un membre, ou par un programme externe (script, intégration continue)
  authentifié au nom d'un membre ou d'un compte de service du groupe. Le lancement planifié et le
  déclenchement par événement externe sont hors périmètre de cette version.
- **FR-002a**: Les administrateurs d'un groupe DOIVENT pouvoir créer, révoquer et limiter des
  comptes de service ; un compte de service n'a accès qu'aux agents qui lui sont explicitement
  attribués, est soumis à ses propres quotas et NE PEUT PAS approuver une action à effet externe.
- **FR-002b**: Un programme externe DOIT pouvoir suivre l'état d'une exécution qu'il a lancée,
  l'arrêter et en récupérer le résultat et la trace.
- **FR-003**: Chaque exécution DOIT avoir des limites explicites (nombre d'itérations, durée,
  budget de coût) ; le dépassement d'une limite DOIT arrêter l'exécution proprement.
- **FR-003a**: Si la plateforme s'interrompt (panne, redémarrage, mise à jour), toute exécution en
  cours DOIT être marquée « interrompue » avec sa trace conservée jusqu'à l'interruption, et un
  membre DOIT pouvoir la relancer de zéro sur la même tâche. Une exécution interrompue NE DOIT
  jamais reprendre automatiquement.
- **FR-003b**: Une exécution en attente d'approbation au moment de l'interruption DOIT être
  conservée avec sa demande d'approbation ; elle reprend après le redémarrage dès que la décision
  est prise, dans la limite du délai d'attente d'approbation.
- **FR-004**: Les membres DOIVENT pouvoir suivre une exécution en cours, étape par étape, et
  l'arrêter à tout moment.
- **FR-005**: La plateforme DOIT mettre en pause tout appel à un outil classé « à effet externe »
  et attendre l'approbation d'un membre habilité (FR-005b) avant de l'exécuter. Les appels aux outils classés
  « lecture seule » s'exécutent sans approbation. Un agent NE PEUT PAS modifier cette
  classification ni s'en exempter.
- **FR-005a**: Chaque outil du catalogue DOIT être classé « lecture seule » ou « à effet externe »
  par un gestionnaire d'outils ; un outil non classé DOIT être traité comme « à effet externe ».
  Pour un serveur MCP, la classification s'applique à chacun des outils qu'il expose.
- **FR-005b**: Seuls peuvent approuver ou refuser une demande d'approbation : le membre qui a
  lancé l'exécution, ou un membre ayant le rôle « approbateur » dans le groupe. Pour une exécution
  lancée par un compte de service, seul un approbateur le peut. Les personnes habilitées DOIVENT
  être notifiées de la demande, et la décision DOIT être consignée dans la trace avec son auteur.
- **FR-006**: Un agent NE DOIT utiliser que les outils explicitement autorisés pour lui ; tout
  appel à un autre outil DOIT être refusé et consigné.

**Traces et observabilité**

- **FR-007**: Chaque exécution DOIT produire une trace complète et horodatée : tâche, étapes,
  appels d'outils et leurs résultats, hooks déclenchés, entrées de mémoire consultées ou modifiées,
  durée, consommation et coût, statut final et motif d'arrêt.
- **FR-008**: Les traces DOIVENT rester consultables après la fin de l'exécution et pouvoir être
  filtrées par agent, membre, statut et période.
- **FR-009**: Les secrets et données personnelles DOIVENT être masqués dans les traces affichées.

**Catalogue d'outils**

- **FR-010**: Les membres habilités DOIVENT pouvoir enregistrer, mettre à jour et retirer des
  serveurs MCP, des skills et des hooks dans le catalogue du groupe.
- **FR-011**: À l'enregistrement d'un serveur MCP, la plateforme DOIT vérifier qu'il est joignable
  et afficher les outils qu'il expose.
- **FR-012**: Les hooks DOIVENT pouvoir se déclencher au minimum au début et à la fin d'une
  exécution, avant et après chaque appel d'outil, et pouvoir autoriser ou bloquer l'appel concerné.
- **FR-012a**: Un hook DOIT être de l'un de ces deux types :
  - **règle déclarative** : une condition portant sur l'agent, l'outil, les paramètres de l'appel
    ou le membre déclencheur, associée à une décision (autoriser, bloquer, journaliser) ;
  - **appel à un service externe** : la plateforme envoie au service enregistré la description de
    l'événement et applique la décision qu'il renvoie.
  La plateforme NE DOIT exécuter aucun code fourni par un membre.
- **FR-012b**: Un hook de type service externe NE DOIT recevoir que les informations nécessaires
  à sa décision ; les identifiants d'accès des outils NE DOIVENT jamais lui être transmis.
- **FR-013**: Un hook en échec ou sans réponse DOIT bloquer l'action concernée par défaut ; pour
  un hook de type service externe, l'absence de réponse dans un délai maximal de 10 secondes vaut
  échec.
- **FR-014**: Les identifiants d'accès des outils DOIVENT être stockés de façon protégée et ne
  DOIVENT jamais être visibles des membres après leur saisie, ni apparaître dans les traces.

**Mémoire**

- **FR-015**: Un agent avec mémoire activée DOIT pouvoir créer, lire et mettre à jour des entrées
  de mémoire persistant d'une exécution à l'autre, chacune rattachée à sa source.
- **FR-016**: Les membres autorisés DOIVENT pouvoir consulter, modifier et supprimer les entrées de
  mémoire ; chaque modification DOIT être historisée.
- **FR-017**: La mémoire DOIT exister à deux niveaux :
  - une **mémoire d'agent**, propre à chaque agent : un agent n'accède jamais à la mémoire d'un
    autre agent ; elle est partagée par tous les membres du groupe qui peuvent lancer cet agent ;
  - une **mémoire de groupe**, partagée : faits communs que tous les agents du groupe pour
    lesquels elle est activée peuvent lire.
- **FR-017a**: Un agent NE DOIT PAS écrire directement dans la mémoire de groupe ; il PEUT
  proposer une entrée, qui n'y entre qu'après validation par un membre ayant le rôle de
  gestionnaire de la mémoire de groupe. Les membres habilités PEUVENT aussi y ajouter des entrées
  eux-mêmes.
- **FR-017b**: La trace d'une exécution DOIT indiquer, pour chaque entrée consultée, si elle vient
  de la mémoire d'agent ou de la mémoire de groupe.
- **FR-018**: Le contenu de la mémoire DOIT être traité comme une donnée et NE DOIT jamais modifier
  les instructions ou les autorisations d'un agent.

**Groupe, rôles et accès**

- **FR-019**: L'accès à la plateforme DOIT exiger une authentification.
- **FR-020**: La plateforme DOIT proposer au minimum les rôles administrateur (gère les membres et
  les rôles), gestionnaire d'outils (gère le catalogue), gestionnaire de la mémoire de groupe
  (valide et corrige la mémoire de groupe), approbateur (approuve ou refuse les appels à des
  outils à effet externe de toute exécution du groupe) et membre (crée et lance des agents). Une même
  personne PEUT cumuler plusieurs rôles.
- **FR-021**: Les agents, outils et traces DOIVENT être partagés entre les membres d'un même
  groupe, dans la limite de leurs rôles.
- **FR-022**: La plateforme DOIT héberger plusieurs groupes isolés les uns des autres : agents,
  outils, identifiants d'accès, mémoires, traces, membres et quotas d'un groupe NE DOIVENT jamais
  être visibles ni utilisables depuis un autre groupe.
- **FR-022a**: Un administrateur de la plateforme DOIT pouvoir créer et désactiver des groupes et
  en désigner les premiers administrateurs ; il NE DOIT PAS accéder au contenu d'un groupe
  (agents, mémoires, traces) sans y avoir été ajouté comme membre.
- **FR-022b**: Une même personne PEUT appartenir à plusieurs groupes, avec un rôle distinct dans
  chacun ; elle DOIT toujours savoir dans quel groupe elle agit.
- **FR-023**: Les administrateurs DOIVENT pouvoir définir des quotas d'exécution et de coût par
  membre ; une exécution qui dépasserait le quota DOIT être refusée.
- **FR-024**: Toute action d'administration (ajout ou retrait de membre, changement de rôle,
  modification du catalogue) DOIT être journalisée avec son auteur et sa date.

### Key Entities *(include if feature involves data)*

- **Groupe**: espace isolé regroupant des membres qui partagent agents, outils, mémoires et
  traces ; la plateforme en héberge plusieurs.
- **Membre**: appartenance d'une personne à un groupe, avec un rôle et des quotas propres à ce
  groupe ; une personne peut être membre de plusieurs groupes.
- **Agent**: configuration réutilisable : nom, instructions, outils autorisés, limites, mémoire
  d'agent activée ou non, accès à la mémoire de groupe activé ou non, auteur.
- **Exécution**: lancement d'un agent sur une tâche ; statut, déclencheur (membre ou compte de
  service) et mode de lancement (manuel ou programmatique), limites appliquées, coût, motif d'arrêt.
- **Compte de service**: identité non humaine d'un groupe utilisée par un programme externe pour
  lancer des agents ; agents attribués, quotas, date de création, état (actif ou révoqué).
- **Trace**: suite ordonnée et horodatée des événements d'une exécution.
- **Outil**: élément du catalogue, de type serveur MCP, skill ou hook, avec sa configuration,
  ses éventuels identifiants d'accès protégés et sa classification (« lecture seule » ou « à effet
  externe », par défaut « à effet externe »).
- **Entrée de mémoire**: information retenue, rattachée soit à un agent (mémoire d'agent), soit
  au groupe (mémoire de groupe), avec sa source, son auteur et son historique de modifications.
- **Proposition de mémoire de groupe**: entrée suggérée par un agent pour la mémoire de groupe,
  en attente de validation, avec sa décision et son auteur.
- **Demande d'approbation**: action en attente de validation humaine, liée à une exécution, avec
  sa décision et son auteur.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: Un nouveau membre crée et lance son premier agent en moins de 10 minutes, sans aide.
- **SC-002**: Chaque étape d'une exécution apparaît dans le suivi en moins de 2 secondes après
  s'être produite.
- **SC-003**: Une demande d'arrêt interrompt l'exécution en moins de 5 secondes dans 99 % des cas.
- **SC-004**: 100 % des appels d'outils non autorisés et des appels non approuvés à des outils
  classés « à effet externe » ou non classés sont bloqués, ce que vérifie un jeu de tests dédié.
- **SC-005**: Aucun secret n'apparaît en clair dans les traces sur un jeu d'exécutions de test
  qui en manipulent.
- **SC-006**: Dans un jeu d'évaluation de tâches répétées, les agents avec mémoire réussissent au
  moins 20 % de tâches de plus que les mêmes agents sans mémoire.
- **SC-007**: La plateforme supporte au moins 20 exécutions simultanées pour un groupe sans que les
  critères SC-002 et SC-003 se dégradent.
- **SC-008**: Un membre retrouve la trace d'une exécution passée et identifie la cause de son
  échec en moins de 3 minutes.
- **SC-009**: 100 % des tentatives d'accès d'un groupe aux données d'un autre groupe (agents,
  outils, identifiants, mémoires, traces) sont refusées, ce que vérifie un jeu de tests dédié.
- **SC-010**: 0 entrée proposée par un agent n'entre dans la mémoire de groupe sans validation
  humaine, sur l'ensemble des exécutions de test.
- **SC-011**: Après une interruption de la plateforme, 100 % des exécutions en cours sont marquées
  « interrompues » avec leur trace, et 100 % des exécutions en attente d'approbation reprennent
  après décision, ce que vérifie un test de redémarrage.

## Assumptions

- Chaque groupe compte de l'ordre de 5 à 50 membres et la plateforme héberge jusqu'à une
  vingtaine de groupes ; la montée en charge au-delà est hors périmètre de cette première version.
- L'authentification s'appuie sur le fournisseur d'identité existant de l'organisation (SSO)
  plutôt que sur des comptes propres à la plateforme.
- Les agents s'appuient sur un ou plusieurs fournisseurs de modèles de langage externes ; le choix
  du fournisseur et du modèle est un paramètre de l'agent et relève de la planification.
- Les membres accèdent à la plateforme depuis un navigateur ; les programmes externes passent par
  un accès programmatique authentifié. Une interface en ligne de commande dédiée peut venir plus
  tard et n'est pas requise ici.
- Les traces et la mémoire sont conservées 90 jours par défaut, durée réglable par un
  administrateur.
- Le délai d'attente d'approbation est de 24 heures et le délai d'expiration d'une proposition de
  mémoire de groupe de 30 jours par défaut ; les deux sont réglables par un administrateur du
  groupe.
- Le format des skills, des hooks et des serveurs MCP suit les conventions publiques en usage
  dans l'écosystème des agents, sans format propriétaire.
- La facturation des coûts aux membres est hors périmètre ; la plateforme mesure et limite les
  coûts mais ne les facture pas.

---
description: Les cinq modes, ce que chacun permet à VOOL de faire sans demander, et ce que couvre chaque bouton d’approbation.
translation: machine
source_sha256: 1f33c8d63bcdeddbd1ac44d3b2d948b354137954ee6accb6ee18eeec8bdda1c6
---

# Permissions

VOOL décide de ce qui peut s’exécuter avant que quoi que ce soit ne s’exécute. Le modèle peut demander une
action ; le mode que vous avez choisi et vos réponses décident si elle a lieu. La lecture est traitée
différemment de la modification, et les secrets, les paiements et les réglages de sécurité sont protégés
dans tous les modes.

## Les modes

Choisissez le mode dans le menu à côté de la zone de message. Une nouvelle discussion démarre en
**Manuel**.

| Mode | S’exécute sans demander | Demande d’abord | Jamais |
| --- | --- | --- | --- |
| Manuel | Lire et lister des fichiers, les commandes sûres et les recherches web publiques en lecture seule | Chaque modification : modifications de fichiers, commandes, actions réseau, réglages, Git, déploiement et messages | Lire les secrets enregistrés |
| Revoir les modifications | Comme Manuel | Comme Manuel. Chaque lot de modifications est présenté comme un changement à examiner avant d’être appliqué | Lire les secrets enregistrés |
| Plan | Lire et lister des fichiers, et les commandes sûres | Rien : il ne fait que lire et proposer un plan | Toute modification, toute action réseau |
| Auto | Lire, créer et modifier des fichiers, les commandes, les actions réseau et de navigateur, et les fournisseurs de modèles | Supprimer, écraser des fichiers existants, installer des dépendances, modifier les réglages des fournisseurs, commit, push, merge et reset Git, déploiement, envoi de messages, paiements et modification des réglages | Lire les secrets enregistrés, modifier les réglages de sécurité, et toute action dont VOOL ne peut pas identifier l’effet |
| Contournement des permissions | Tout sauf les quatre classes protégées | Paiements | Lire les secrets enregistrés, modifier les réglages de sécurité, et toute action dont VOOL ne peut pas identifier l’effet |

Un mode, une compétence ou un plugin ne peut que restreindre ce qui est proposé. Aucun d’eux n’accorde
une action que le mode n’autorise pas.

## Répondre à une demande de permission

Quand une action a besoin de vous, une barre **Permission requise** s’ouvre au-dessus de la zone de
message. Elle nomme l’action exacte : la ligne de commande qui va s’exécuter, ou les fichiers qui vont
changer, si le changement peut être annulé, et un aperçu des modifications. Rien ne s’exécute avant que
vous répondiez, et la demande vous attend même si VOOL redémarre. Une demande dont le délai est écoulé
apparaît comme expirée et ne peut pas être approuvée.

La barre ne propose que les choix adaptés à l’action :

| Bouton | Ce qu’il autorise |
| --- | --- |
| Autoriser une fois | Cette seule action exacte. |
| Autoriser pour cette tâche | La même action exacte à nouveau pendant cette tâche, dans le même mode. Proposé pour les actions qui ne sont pas des modifications de fichiers. |
| Autoriser les *N* modifications prévues pour cette requête | Les modifications de fichiers que cette requête a déjà prévues, les fichiers exacts listés sous Voir les détails, et rien d’autre. |
| Autoriser les modifications de l’espace de travail dans ce chat | Les lectures, les nouveaux fichiers et les modifications dans l’espace de travail de cette discussion, pendant huit heures ou jusqu’au redémarrage de VOOL ou à la suppression de la discussion. |
| Autoriser dans ce projet | Les lectures et les écritures de fichiers dans le dossier du projet. Cela reste valable pour le projet, aussi après un redémarrage et dans les nouvelles discussions. |
| Voir les détails | Affiche la demande complète avant que vous décidiez. |
| Refuser | L’action ne s’exécute pas. |

Quoi que vous autorisiez, les suppressions, déplacements, écrasements, commandes, actions réseau, secrets
et paiements demandent toujours séparément à chaque fois, sauf si le mode lui-même les autorise.

## Contournement des permissions

Le contournement exécute les outils disponibles sans demandes individuelles, dans un périmètre et pour une
durée que vous choisissez :

* **Périmètre :** la tâche actuelle uniquement, cette session de discussion ou ce projet.
* **Durée :** 15 ou 30 minutes, 1, 2, 4 ou 8 heures, une durée personnalisée de 15 à 1440 minutes,
  ou *Jusqu’à ce que je le désactive*, pour cette discussion uniquement.

Un contournement limité dans le temps fonctionne dans n’importe quelle discussion.
*Jusqu’à ce que je le désactive* exige que la discussion soit liée à un dossier de projet, et ce dossier
ne peut pas être tout votre dossier personnel, la racine du disque, ni un dossier qui contient les données
de VOOL lui-même. Tant que le contournement est actif, un bandeau affiche son périmètre et quand il prend
fin, et son **×** le désactive immédiatement ; la discussion revient en Manuel. Supprimer la discussion met
aussi fin à son contournement. Le contournement n’accorde jamais d’accès en dehors de l’espace de travail
et ne peut pas permettre à un modèle d’élargir ses propres permissions. Les paiements demandent toujours,
et les secrets et les réglages de sécurité restent hors d’atteinte.

## Paiements et dépenses

Approuver une action n’approuve jamais un paiement. Les paiements passent par la proposition et
l’approbation propres au portefeuille, avec ses limites, dans tous les modes. Les appels de modèle payants
sont retenus par vos plafonds de dépenses et vos garde-fous de prix avant que le fournisseur ne soit
contacté. Voir [Portefeuille](../guides/wallet.md) et
[Limites de dépenses et garde-fous de prix](../guides/spending-limits.md).

## Quand une action est refusée

Une action refusée n’a rien changé, et le message indique ce qui a été refusé et pourquoi. Les codes que
vous pouvez voir sont [`permission_denied`](../ERROR_BOOK.md#permission-denied),
[`confinement_refusal`](../ERROR_BOOK.md#confinement-refusal) pour une modification en dehors du dossier
autorisé, et [`tool_unavailable`](../ERROR_BOOK.md#tool-unavailable) pour un outil que ce mode ne
propose pas. Voir [Erreurs et codes d’erreur](../reference/errors.md).

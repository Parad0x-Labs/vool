---
description: Comment utiliser VOOL comme assistant IA de programmation sur votre propre base de code — définir l’espace de travail, poser des questions sur le code et relire les modifications.
translation: machine
source_sha256: 1d1c4e7c4222eed4dc6caae24dca70729496ead8b88b1fc45a81a61ed548241a
---

# Coder avec VOOL

## Placer l’espace de travail sur le dépôt

Faites pointer l’[espace de travail](../concepts/workspaces.md) vers la racine du projet. VOOL lit les
fichiers dont il a besoin plutôt que de charger toute l’arborescence.

## Demander en termes de code

Nommer un fichier ou un symbole donne au runtime un point de départ :

> Trouve où le délai d’expiration de la session est défini dans `src/auth/` et explique ce qui se passe
> quand il expire.

## Relire avant d’appliquer

En mode Manuel, chaque modification demande d’abord. En mode Auto, les modifications et les commandes
courantes peuvent s’exécuter automatiquement et apparaissent dans le journal d’activité, tandis que les
suppressions et les écrasements demandent toujours. Faites un commit avant un changement important pour
que le diff soit facile à lire et facile à annuler.

## Local ou cloud

La programmation fonctionne dans les deux modes. Un modèle local de code 7B gère la plupart des
modifications courantes ; les modèles cloud plus grands gèrent les raisonnements plus longs. L’indicateur
de modèle vous dit lequel est actif avant l’envoi : `LOCAL` pour un modèle local, `CLOUD` pour un modèle cloud,
et par défaut `AUTO` (`VOOL Auto`, `Local first`) lorsque VOOL choisit.

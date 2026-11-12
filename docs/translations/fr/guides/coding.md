---
description: Comment utiliser VOOL comme assistant IA de programmation sur votre propre base de code — définir l’espace de travail, poser des questions sur le code et relire les modifications.
translation: machine
source_sha256: 112dfd866a96b0f7fd22f6f57f7f6480d7ac293466fdf0260a74f3bc0b870036
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
de mode vous dit lequel est actif avant l’envoi.

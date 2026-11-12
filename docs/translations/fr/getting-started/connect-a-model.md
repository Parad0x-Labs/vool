---
description: Comment connecter un modèle d’IA à VOOL : un modèle local qui tourne sur votre propre ordinateur, ou un fournisseur cloud avec votre propre clé API.
translation: machine
source_sha256: fdc85c0cf7cf8399666b8dd3d49ddbfb7cbe65ca7800be4630d88bd86053987a
---

# Connecter un modèle

VOOL ne fournit pas de modèle. Vous en choisissez un, et ce choix détermine si une demande
reste sur votre machine ou la quitte.

## Local

Si un moteur local est installé et en marche, VOOL le détecte et liste ses modèles.
En sélectionner un fait afficher Local à l’indicateur. Les détails de configuration se trouvent dans
[Exécuter un modèle local](../../../guides/local-models.md).

## Cloud

Ajoutez une clé de fournisseur dans **Réglages → Clés API**. La clé est conservée dans un fichier chiffré sur cet
ordinateur (voir [Modèle de sécurité](../../../trust/security.md#keys)) et sert à envoyer les requêtes
**depuis votre machine directement au fournisseur**.

Sélectionner un modèle cloud fait afficher le nom du fournisseur à l’indicateur, et celui-ci change avant
qu’aucune requête ne soit envoyée.

Voir [Connecter OpenRouter](../../../guides/openrouter.md) pour la marche à suivre propre à OpenRouter.

## Changer de modèle

Changer de modèle change de mode. L’indicateur fait foi : s’il affiche Local,
la prochaine requête est traitée sur cet ordinateur.

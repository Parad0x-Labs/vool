---
description: Quels modèles d’IA fonctionnent avec VOOL — des modèles locaux sur votre propre matériel et des modèles cloud via votre compte chez un fournisseur.
translation: machine
source_sha256: 1528dfca8e391868d09156c42c1dd51514770ffab17077d9260d248842c82060
---

# Modèles pris en charge

VOOL n’intègre pas de modèle. Il se connecte aux moteurs et aux fournisseurs que vous choisissez.

## Moteurs locaux

Tout moteur qui expose un point de terminaison compatible OpenAI sur localhost est utilisable. La
détection est automatique pour les plus courants.

## Fournisseurs cloud

Les modèles cloud sont atteints avec votre clé, depuis votre machine. En sélectionner un fait passer
l’app en mode cloud (l’indicateur de mode affiche le nom du fournisseur) — voir
[Local et cloud](../concepts/local-and-cloud.md).

[OpenRouter](../guides/openrouter.md) est la connexion unique la plus large : une clé, de nombreux
fournisseurs.

## Choisir

| Travail | Choix raisonnable |
| --- | --- |
| Fichiers privés, pas de réseau | Local, 7B ou plus |
| Code au quotidien | Modèle local de code 7–14B |
| Raisonnement long | Un modèle cloud plus grand |
| Maîtrise des coûts | Le local d’abord, le cloud quand cela en vaut la peine |

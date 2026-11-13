---
description: Quels modèles d’IA fonctionnent avec VOOL — des modèles locaux sur votre propre matériel et des modèles cloud via votre compte chez un fournisseur.
translation: machine
source_sha256: 2ce84ebed0545059de772cb175bd8f088ee2d22feb60d4256ee7800f20097824
---

# Modèles pris en charge

VOOL n’intègre pas de modèle — le premier modèle local est téléchargé la première fois que vous
l’utilisez. Il se connecte aux moteurs et aux fournisseurs que vous choisissez.

## Moteurs locaux

Ollama, llama.cpp, vLLM et MLX sont détectés automatiquement. Les autres moteurs qui exposent un point
de terminaison compatible OpenAI sur localhost peuvent être indiqués à la main.

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

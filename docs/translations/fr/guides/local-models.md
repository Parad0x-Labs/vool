---
description: Comment exécuter des modèles d’IA en local sur votre propre ordinateur avec VOOL — installer un serveur de modèles local, choisir une taille de modèle et travailler entièrement hors ligne.
translation: machine
source_sha256: d93b9995b0f4fec8aca12bb90d8d6f9ff0fd9623c43d4529913dc76f055ac24c
---

# Exécuter un modèle local

## Installer un moteur local

VOOL communique avec un serveur de modèles local. L’app macOS inclut son propre exécuteur de modèles
locaux, donc sur un Mac vous pouvez sauter cette étape. Sous Linux et pour les installations depuis les
sources, installez un serveur comme Ollama, démarrez-le et téléchargez un modèle.

```bash
ollama serve
ollama pull qwen2.5-coder:7b
```

## Diriger VOOL vers lui

Ouvrez **Réglages → Modèles et fournisseurs**. Les moteurs locaux en marche sont détectés
automatiquement. Sélectionnez un modèle et l’indicateur de mode passe à Local.

## Le certifier une fois

Un modèle local n’écrit des réponses qu’une fois certifié. Ouvrez
**Réglages → Modèles et fournisseurs → Modèles locaux**, enregistrez le modèle, puis choisissez
**Certify**. La certification lance une courte vérification des outils sur votre machine ; rien n’est
téléchargé et personne n’est contacté. Tant qu’un modèle n’a pas réussi, VOOL ne le laisse pas écrire de
réponses finales.

## Dimensionnement

| Taille du modèle | Mémoire vive nécessaire | Adapté à |
| --- | ---: | --- |
| 3B | ~4 Go | Petites modifications, questions rapides |
| 7–8B | ~8 Go | Usage général, la plupart du code |
| 14B | ~16 Go | Raisonnements plus longs |
| 32B+ | 32 Go ou plus | Travaux plus lourds, plus lent |

{% hint style="info" %}
Les versions quantifiées réduisent fortement la mémoire vive nécessaire. Un modèle 7B en 4 bits tourne
confortablement dans environ 5 Go.
{% endhint %}

## S’il est lent

* Choisissez un modèle plus petit ou plus fortement quantifié.
* Fermez les autres applications gourmandes en mémoire vive.
* Raccourcissez le contexte — moins de fichiers dans la requête, c’est moins à traiter.

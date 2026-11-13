---
description: Comment exécuter des modèles d’IA en local sur votre propre ordinateur avec VOOL — installer un serveur de modèles local, choisir une taille de modèle et travailler entièrement hors ligne.
translation: machine
source_sha256: a19d4790729d13ed5e1b134bcb67ed3e7a80609561be4227ab9480492100c8a6
---

# Exécuter un modèle local

## Installer un moteur local

VOOL utilise Ollama par défaut pour exécuter les modèles locaux ; d’autres moteurs locaux avec un point de
terminaison compatible OpenAI fonctionnent aussi. Ollama est un programme distinct, installé séparément
depuis [ollama.com](https://ollama.com). L’app macOS inclut son propre exécuteur de modèles
locaux, donc sur un Mac vous pouvez sauter cette étape. Sous Linux et pour les installations depuis les
sources, installez Ollama, démarrez-le et téléchargez un modèle.

```bash
ollama serve
ollama pull qwen3:4b
```

VOOL choisit le modèle local pour vous par défaut — un modèle Qwen adapté à votre ordinateur. Sur
beaucoup d’ordinateurs, ce modèle est `qwen3:4b` ; sur les ordinateurs avec 32 Go de mémoire ou plus, le
modèle par défaut est un modèle Qwen plus grand.

## Diriger VOOL vers lui

Ouvrez **Réglages → Modèles et fournisseurs**. Les moteurs locaux en marche sont détectés
automatiquement. Sélectionnez un modèle et l’indicateur de mode passe à Local.

## Le préparer une fois

Un modèle local n’écrit des réponses qu’une fois que VOOL l’a vérifié. Ouvrez
**Réglages → Modèles et fournisseurs → Modèles locaux** et appuyez sur **Utiliser ce modèle** à côté du modèle.
VOOL vérifie discrètement, sur votre ordinateur, que le modèle sait assez bien utiliser les outils de VOOL pour
répondre à votre place. La vérification peut prendre une minute ou deux ; rien n’est téléchargé et personne n’est
contacté. Lorsqu’elle réussit, le modèle affiche **Prêt** et VOOL répond avec lui. D’ici là, VOOL ne le laisse pas
écrire de réponses finales.

* **Ne convient pas :** le modèle ne sait pas assez bien utiliser les outils de VOOL. VOOL indique ce que vous pouvez
  faire à la place : utiliser ou télécharger le modèle qu’il a choisi pour cet ordinateur, ou utiliser un modèle cloud.
* **Vérification inachevée :** assurez-vous qu’Ollama est ouvert, puis appuyez sur **Réessayer**. Un ordinateur lent
  peut avoir besoin d’un deuxième essai.

Si aucun modèle local n’est prêt au démarrage de VOOL, il vérifie jusqu’à trois des modèles de cet ordinateur,
en commençant par celui que vous avez choisi, et utilise le premier qui réussit. Il ignore un modèle trop gros pour cet
ordinateur. Un modèle prêt passe toujours avant un modèle qui ne l’est pas.

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

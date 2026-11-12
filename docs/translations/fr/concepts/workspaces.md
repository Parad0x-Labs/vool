---
description: Un espace de travail est le dossier de projet dans lequel VOOL travaille. En dehors, VOOL peut lire vos dossiers personnels visibles et ne modifier des fichiers que dans Bureau, Téléchargements et Documents.
translation: machine
source_sha256: 33b648a0a55f99b1c96365553942911862672eafa55e94bd45f18ce566d5fcea
---

# Espaces de travail

Un espace de travail est le dossier de projet dans lequel une discussion travaille. VOOL y lit et y modifie
des fichiers, en demandant d’abord chaque fois que votre mode de fonctionnement l’exige (voir
[Outils](tools.md)).

## Ajouter et supprimer

Choisissez un dossier avec **Choisir un dossier de projet…** dans la discussion, ou avec **+ Projet** dans
la barre latérale de discussion. Supprimer un projet fait oublier à VOOL ce qu’il y a appris et replace
ses discussions dans Général. Vos fichiers ne sont pas touchés.

## Vos autres dossiers

En dehors de l’espace de travail, les outils de fichiers de VOOL peuvent aussi accéder à votre dossier
personnel, avec des limites fixes :

| Dossiers | Ce que VOOL peut faire |
| --- | --- |
| Bureau, Téléchargements, Documents | Lire, et modifier des fichiers en demandant d’abord, comme le prévoit votre mode de fonctionnement |
| Images, Films (Vidéos sous Linux), Musique | Lecture seule : lister, trouver, ouvrir et mesurer, jamais modifier |
| Vos autres dossiers personnels visibles, comme `~/Projects` | Lecture seule |
| Dossiers cachés (comme `~/.ssh`), données des apps et du système (`Library`, `Applications`, `AppData`), dossier de données de VOOL lui-même | Jamais |

VOOL ne regarde dans ces dossiers que lorsqu’une demande en a besoin, par exemple « trouve mon PDF
d’impôts » ou « quelle vidéo dans Films est la plus grande ». Il n’indexe pas votre disque en
arrière-plan.

## Conseils de périmètre

* Faites pointer un espace de travail vers un projet, pas vers votre dossier personnel.
* Gardez les fichiers d’identifiants, les clés et les coffres de mots de passe dans un gestionnaire de mots
  de passe ou dans un dossier caché, pas en vrac dans Bureau, Téléchargements, Documents ou un dossier de
  projet.
* Utilisez un projet distinct pour chaque travail, afin que le contexte de l’un n’atteigne pas l’autre.

{% hint style="warning" %}
Tout ce que VOOL lit, dans le dossier de projet ou dans vos dossiers personnels, peut faire partie de ce qui
est envoyé à votre fournisseur quand un modèle cloud est sélectionné. Délimitez le périmètre avec soin.
{% endhint %}

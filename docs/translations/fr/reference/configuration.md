---
description: Où VOOL stocke sa configuration et ce que signifient les réglages.
translation: machine
source_sha256: 3bbb23b29003bdd8c973c2d88a34faf974f6436405b7c90da4d0307c0962f887
---

# Configuration

## Emplacements

VOOL conserve ses réglages, ses discussions, sa mémoire et ses enregistrements dans un seul « runtime home » :

| Installation | Runtime home |
| --- | --- |
| App macOS | `~/Library/Application Support/VOOL/runtime` |
| Archive tar Linux et installations depuis les sources | `~/.vool_runtime` |
| Windows (expérimental, depuis les sources) | `%LOCALAPPDATA%\VOOL` |

Définir `VOOL_HOME` avant de démarrer VOOL fait utiliser ce dossier à la place. Les journaux de démarrage
affichent le « runtime home » utilisé.

Les clés ne sont **pas** écrites dans les fichiers de réglages ni dans les journaux. Elles sont conservées
chiffrées dans un fichier d’identifiants distinct dans ce « runtime home », lisible uniquement par votre
compte utilisateur ; les clés que VOOL conservait déjà dans le trousseau macOS sur un Mac y restent. Voir
[Modèle de sécurité](../trust/security.md#clés).

## Réglages

Modifiez les réglages dans l’app sous **Réglages**. Ceux qui décident de ce que VOOL peut faire :

* **Modèle** — le modèle actif, qui décide si un tour est LOCAL ou CLOUD.
* **Espace de travail** — le dossier de projet dans lequel travaille une discussion ; voir
  [Espaces de travail](../concepts/workspaces.md) pour les autres dossiers auxquels VOOL peut accéder.
* **Mode de fonctionnement** — choisi dans le menu à côté de la zone de message, pas ici. Manuel demande
  avant chaque modification ; Auto laisse s’exécuter les modifications et commandes courantes, tandis que
  la suppression, l’écrasement, l’installation, les dépenses et la modification des réglages demandent
  toujours. Voir [Permissions](../concepts/permissions.md).
* **Accès web et crypto** — tous deux restent désactivés jusqu’à ce que vous les activiez.

VOOL n’a pas de réglage de télémétrie produit, car il n’envoie aucune télémétrie produit.

## Réinitialisation

Quittez VOOL et déplacez le « runtime home » ailleurs. Les fichiers de l’espace de travail ne sont pas
touchés ; VOOL redémarre avec un profil neuf. Vos clés enregistrées sont déplacées avec lui, donc
ajoutez-les de nouveau dans **Réglages → Clés API**. Les clés conservées dans le trousseau macOS y restent
jusqu’à ce que vous les supprimiez dans Trousseau d’accès.

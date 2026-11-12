---
description: Les solutions aux problèmes les plus fréquents.
translation: machine
source_sha256: 809bae11bc4bf8c2d707cfef616f80056a5d98d35b0786cc937a40526e802689
---

# Dépannage

## macOS refuse d’ouvrir l’app

C’est attendu : l’app n’est pas encore notarisée. Vérifiez d’abord la somme de contrôle, puis ouvrez
**Réglages Système → Confidentialité et sécurité** et cliquez sur **Ouvrir quand même**. Sous macOS 14, vous
pouvez à la place cliquer sur VOOL en maintenant la touche Contrôle enfoncée et choisir **Ouvrir**.

## Windows

Il n’y a pas de téléchargement Windows dans cette version. Sous Windows, VOOL s’installe depuis les sources et
reste expérimental ; voir le [guide d’installation](../getting-started/install.md#installer-depuis-les-sources).

## Aucun modèle local n’est listé

1. Vérifiez que le moteur local est en marche.
2. Vérifiez qu’au moins un modèle est téléchargé.
3. Vérifiez que le port correspond à celui qu’attend VOOL.

```bash
curl http://localhost:11434/api/tags
```

Si cette commande ne renvoie rien, le moteur local n’est pas démarré.

## Les réponses sont lentes

En général, c’est la taille du modèle par rapport à la mémoire vive disponible. Voir
[Exécuter un modèle local](../../../guides/local-models.md).

## Une erreur affiche un code

Chaque échec a un code stable. [Erreurs et codes d’erreur](../../../reference/errors.md) explique comment
le lire, et le [Livre des erreurs](../../../ERROR_BOOK.md) indique pour chaque code ce qui s’est passé, ce
qui a peut-être déjà eu lieu et ce qu’il faut faire ensuite.

## Repartir de zéro

Quittez VOOL et supprimez le dossier « runtime home » indiqué dans
[Configuration](../../../reference/configuration.md). Cela efface vos discussions, votre mémoire, vos
réglages, vos clés enregistrées et tout portefeuille que VOOL conserve sur cet ordinateur.
Sauvegardez donc d’abord ce dont vous avez besoin (voir
[Sauvegarde et restauration](../../../help/backup-and-recovery.md)). Les fichiers de l’espace de travail ne sont
pas touchés.

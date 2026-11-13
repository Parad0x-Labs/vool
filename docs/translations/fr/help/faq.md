---
description: Questions fréquentes sur VOOL — s’il fonctionne hors ligne, où votre clé API est stockée, s’il est gratuit et quels systèmes il prend en charge.
translation: machine
source_sha256: 4366d0e907cc16c3206be768f984f5a525f4187024d16aae541a2ec8937e0aa0
---

# FAQ

## VOOL fonctionne-t-il hors ligne ?

Oui, avec un modèle local, pour vos conversations et vos fichiers. Les modèles cloud ont besoin d’une connexion
réseau, par définition. Les informations en direct comme la météo ou les prix viennent de services web publics uniquement lorsque Recherches web est activé (désactivé sur une nouvelle installation), et jamais en Local uniquement.

## Ai-je besoin d’une clé cloud ?

Non. Un modèle local suffit pour utiliser l’app.

## Où ma clé est-elle stockée ?

Dans un fichier chiffré dans le « runtime home » de VOOL sur votre ordinateur, lisible uniquement par
votre compte utilisateur. Les clés que VOOL conservait déjà dans le trousseau macOS sur un Mac y restent.
Les clés restent sur votre machine. Voir [Modèle de sécurité](../trust/security.md#clés).

## Parad0x voit-il mes prompts ?

Pas en mode local, ni en mode cloud — les requêtes cloud vont de votre machine à votre fournisseur. Voir
[Traitement des données](../trust/data-handling.md).

## Pourquoi mon système m’avertit-il quand je l’ouvre ?

Les apps macOS sont signées ad hoc mais pas encore notarisées par Apple, donc macOS vous demande de
confirmer la première ouverture. Les étapes se trouvent dans
[Installation](../getting-started/install.md#première-ouverture-sur-un-mac). Voir
[État des versions](../trust/release-status.md).

## Puis-je utiliser mon propre modèle ?

Oui. N’importe quel moteur qui expose un point de terminaison local compatible OpenAI. Voir
[Modèles pris en charge](../reference/models.md).

## Est-ce gratuit ?

L’application est gratuite pendant la bêta. L’utilisation des modèles cloud est facturée par votre
fournisseur, sur votre compte.

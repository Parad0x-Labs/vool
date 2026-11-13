---
description: Comment connecter OpenRouter à VOOL pour qu’une seule clé API donne accès à de nombreux modèles d’IA dans le cloud.
translation: machine
source_sha256: 75cb20d43eb37adb1d30913959ca1cb46864ad57ba7a1a22ec261f0e0eaf2e9a
---

# Connecter OpenRouter

OpenRouter donne accès à de nombreux fournisseurs derrière une seule clé. VOOL permet d’ajouter une
clé OpenRouter.

## Ajouter la clé

Créez une clé dans votre compte OpenRouter, collez-la sous **Réglages → Clés API** et appuyez sur
**Enregistrer la clé**. VOOL vérifie la clé une fois auprès d’OpenRouter, puis la conserve dans un
fichier chiffré sur cet ordinateur (voir [Modèle de sécurité](../trust/security.md#clés)).

## Facturation et attribution

{% hint style="info" %}
Votre compte OpenRouter reste le vôtre. VOOL envoie les requêtes directement depuis cet ordinateur
avec votre clé. Parad0x ne relaie pas vos prompts et ne paie pas votre utilisation. L’utilisation réelle
de VOOL peut apparaître dans les statistiques OpenRouter publiques et agrégées de VOOL, mais les prompts
individuels et les identités ne sont pas publiés.
{% endhint %}

## Retirer la clé

Supprimez la clé sous **Réglages → Clés API**. Révoquez aussi la clé dans votre compte OpenRouter si vous voulez qu’elle soit désactivée côté serveur.

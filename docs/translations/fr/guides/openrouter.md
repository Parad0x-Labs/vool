---
description: Comment connecter OpenRouter à VOOL pour qu’une seule clé API donne accès à de nombreux modèles d’IA dans le cloud.
translation: machine
source_sha256: 3a09fe6c636e2b8dd14a2d627c4653055dbb10bf8fd8221b7a3bcccf3044cb19
---

# Connecter OpenRouter

OpenRouter donne accès à de nombreux fournisseurs derrière une seule clé. VOOL permet de connecter un
compte OpenRouter.

## Connecter

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

## Déconnecter

Supprimez la clé sous **Réglages → Clés API**. Révoquez aussi la clé dans votre compte OpenRouter si vous voulez qu’elle soit désactivée côté serveur.

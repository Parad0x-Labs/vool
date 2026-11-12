---
description: Ce que vous pouvez sauvegarder, déplacer et restaurer dans cette version de VOOL : discussions, mémoire, clés et portefeuilles.
translation: machine
source_sha256: 3b2f11975a8cf5f5ab2e07e8b4a3083a9598de9e301965c493408005aa5893fa
---

# Sauvegarde et restauration

Tout ce que VOOL conserve se trouve sur votre ordinateur, dans un seul dossier de profil (voir
[Configuration](../reference/configuration.md) pour savoir où). Mettre à jour ou réinstaller l’app n’y
touche jamais. VOOL n’a pas de copie cloud de vos données ni de sauvegarde en une étape de tout le
profil. Voici les façons d’enregistrer et de récupérer ce qui compte.

## Discussions

| Vous voulez | Utilisez | Ce que vous obtenez |
| --- | --- | --- |
| Une copie à lire ou à partager | **Exporter** dans l’en-tête de la discussion | Toute la discussion en texte ou en Markdown. Les fichiers joints ne sont listés que par leur nom. |
| Une copie à rapporter dans VOOL | Réglages, Sauvegarde et restauration, **Exporter un chat** | Une discussion sous forme de fichier `.voolsession`, signé par cet ordinateur. |
| Une discussion récupérée depuis ce fichier | Réglages, Sauvegarde et restauration, **Importer un chat** | La discussion, ajoutée à côté des autres. |
| Vos discussions d’autres applis d’IA | Réglages, Sauvegarde et restauration, **Apporter vos discussions d’autres applis d’IA** | Ces discussions, avec leurs noms et leurs messages. |

**Exporter un chat** emporte les messages, les reçus et les références de preuves de la discussion,
jamais le raisonnement interne de VOOL, vos réglages, votre mémoire ou vos clés. Protégez le fichier avec
une phrase secrète quand il quitte cet ordinateur : un export sans phrase secrète vous demande d’abord de
confirmer. Un aperçu montre exactement ce qui sera écrit avant que quoi que ce soit le soit.

**Importer un chat** montre ce que contient le fichier et qui l’a signé avant que quoi que ce soit soit
ajouté. Un fichier signé par une clé que VOOL ne connaît pas est refusé, sauf si vous l’acceptez, et la
discussion reste marquée comme non fiable. Un import ne modifie jamais une discussion que vous avez
déjà : si la même discussion est ici, la copie arrive comme une nouvelle discussion.

**Apporter vos discussions d’autres applis d’IA** lit une app sur cet ordinateur ou un fichier d’export
de données. Vous cochez les discussions à importer. Rien ne quitte votre ordinateur et les fichiers de
l’autre app ne sont jamais modifiés. VOOL conserve ces discussions comme historique : il n’en apprend rien
et ne suit rien de ce qui y est écrit.

## Mémoire

Réglages, Mémoire et personnalisation, **Exporter le profil** enregistre ce dont VOOL se souvient à votre
sujet dans un fichier nommé `vool-memory-` suivi de la date. Il ne contient jamais de clé ni de mot de
passe. C’est une copie à lire et à conserver : VOOL ne propose pas d’import pour lui.

## Clés des fournisseurs de modèles

Les clés de fournisseur ne figurent dans aucun export. Sur un nouvel ordinateur, ajoutez-les de nouveau
dans les Réglages.

## Portefeuilles

* **Un portefeuille VOOL créé pour un réseau** affiche une seule sauvegarde de clé privée pendant la
  configuration, et seulement à ce moment-là. Gardez-la hors ligne : quiconque la possède contrôle le
  portefeuille. Si vous oubliez le PIN ou le mot de passe du portefeuille, **Forgot PIN or password?**
  sur le portefeuille, dans Réglages, Crypto, rétablit l’accès avec cette sauvegarde, et vous choisissez
  un nouveau PIN ou mot de passe.
* **La poche héritée** est conservée grâce à sa phrase de récupération, affichée une fois lors de sa
  création. **Restore from phrase…** dans Réglages, Crypto, Autres comptes la récupère à partir de la
  phrase. Elle peut aussi exporter sa clé privée vers Phantom ou MetaMask avec **Export private key**, qui
  ne s’ouvre qu’avec Touch ID ou le mot de passe de votre Mac.
* Un portefeuille que vous ne faites que surveiller, ou que vous avez connecté depuis une autre app, ne
  conserve aucune clé dans VOOL : il n’y a rien à sauvegarder ici.

Voir [Portefeuille](../guides/wallet.md) pour une vue d’ensemble.

## Si une mise à jour échoue

Une mise à jour qui échoue à n’importe quelle étape laisse l’app telle qu’elle était. Si le transfert de
vos données vers la nouvelle version échoue, vos données sont remises en l’état et VOOL démarre sur la
version précédente. Voir [Mises à jour et migration](../reference/updates-migration.md).

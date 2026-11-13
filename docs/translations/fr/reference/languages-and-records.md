---
description: Langues, accessibilité, historique d’activité, reçus, et comment les réponses des modèles sont vérifiées.
translation: machine
source_sha256: 519707395e736b24ba96005597cca3bc59dcf12ca85d905c70f8a50884c5bba2
---

# Langues, accessibilité et enregistrements

## Langues de l’interface

L’interface de VOOL est disponible en plusieurs langues ; les textes plus récents peuvent encore
s’afficher en anglais en attendant leur traduction. Le choix de la langue ne concerne que la
présentation : les décisions de permission, l’autorité de dépense et les codes d’erreur sont
**indépendants de la langue par construction** — un message d’erreur traduit ne peut jamais élargir ni
restreindre ce que l’autorité anglaise a décidé.

## Accessibilité

* Des raccourcis clavier pour les principaux parcours (voir [Raccourcis clavier](shortcuts.md)).
* Une présentation claire et sombre dans la documentation et dans l’app, qui suit la préférence de votre
  système, avec un choix manuel possible.
* Les erreurs sont indiquées par du texte, pas seulement par la couleur ; les actions destructrices
  exigent toujours une confirmation explicite plutôt qu’un simple clic malheureux.

## Activité

La vue Activité est l’historique complet de ce que VOOL a fait : les appels d’outils avec leurs entrées
et leurs résultats, les approbations que vous avez accordées ou refusées, et les paiements avec leurs
reçus. Un tour arrêté par un garde-fou montre l’arrêt et le garde-fou — un refus est un événement
enregistré, pas une absence.

## Reçus

Les actions qui touchent à l’argent ou aux dépenses écrivent des **reçus** : ce qui a été proposé, ce qui
a été approuvé, ce qui a été envoyé et ce qui a été réglé. Les résultats inconnus sont enregistrés comme
inconnus — jamais racontés comme un succès ou un échec. C’est ce qui rend
*« check its status before retrying »* applicable : le reçu vous dit dans quel état se trouve réellement
le paiement.

## État de vérification des modèles

VOOL distingue ce qu’un modèle a **dit** de ce que le runtime a **prouvé** :

* Les résultats des outils sont enregistrés avec leurs traces d’exécution ; les affirmations d’une
  réponse sur des fichiers, des paiements ou des données sont liées à ces traces, et les affirmations non
  étayées sont retenues.
* Les reçus portent un statut de vérification ; là où une voie est expérimentale ou invérifiable dans
  cette version, le statut le dit au lieu de laisser croire à une preuve.

Voir [Reçus](../concepts/receipts.md) pour le concept et [Erreurs et codes d’erreur](errors.md) pour la
façon dont les échecs sont classés.

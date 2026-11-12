---
description: Contacts — personnes et points de terminaison enregistrés, avec un examen des changements qui protège chaque modification.
translation: machine
source_sha256: 8be5d771229a4d6c4b439af014f0407649ed694fd26f9fd880009d25f5b8a347
---

# Contacts

La liste de contacts est un carnet d’adresses local et structuré (des personnes, leurs points de
terminaison et leurs alias de paiement) que VOOL peut utiliser au lieu de vous demander de retaper une
adresse. Comme un carnet d’adresses alimente des paiements et des messages, ses modifications sont
**protégées** : examinées, confirmées et enregistrées.

## Modifications protégées

Modifier un contact enregistré se fait en deux étapes :

1. **Examen** — la modification est préparée et affichée. Si l’entrée a changé depuis que vous l’avez
   ouverte (une autre fenêtre, un autre appareil), la modification est refusée avec
   *« changed after this was reviewed; review it again. Nothing changed. »*
2. **Confirmation** — la modification n’est validée qu’avec votre PIN ou votre mot de passe dans l’écran
   d’examen.

Chaque refus indique clairement que **rien n’a été enregistré ni modifié** — parce que rien ne l’a été.
Si votre PIN a été accepté mais que l’enregistrement lui-même a échoué, le message le dit exactement : le
PIN a été accepté ; enregistrez de nouveau avant que la confirmation n’expire.

## Garde-fous

* Une seule modification par contact à la fois ; un nombre limité de modifications en attente.
* Supprimer un contact ne réécrit pas l’historique en silence : les références à un contact supprimé sont
  signalées par *« That contact was deleted »* — jamais résolues de nouveau en silence.
* Les importations ne s’appliquent jamais partiellement : si la source n’a pas pu être lue, rien n’a été
  importé.

## Pourquoi cette conception

Une adresse de paiement mal saisie, c’est de l’argent irrécupérable. L’étape d’examen existe pour
qu’aucun tour de modèle, aucune modification en masse et aucune fenêtre périmée ne puisse changer la
personne à laquelle « Alice » renvoie sans qu’un humain confirme exactement l’entrée qui en résulte.

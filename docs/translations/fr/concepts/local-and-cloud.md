---
description: Quelles données quittent votre ordinateur quand vous utilisez VOOL, et quand. La différence entre exécuter l’IA en local et utiliser un modèle cloud.
translation: machine
source_sha256: 3217e13bf6d684853b92d19097627d07e64bdd17745560b1204871cb0b7b0753
---

# Local et cloud

VOOL a deux états. L’interface montre toujours lequel est actif.

## Local

Tout reste sur cet ordinateur. Le modèle s’exécute sur un moteur local, les fichiers sont lus depuis le
disque, et aucune requête n’est envoyée à un fournisseur.

## Cloud (nom du fournisseur)

Le contexte sélectionné est envoyé directement au fournisseur que vous avez sélectionné, depuis votre
machine, avec votre clé.

« Le contexte sélectionné » désigne ce que l’app a assemblé pour cette requête : votre prompt, plus les
fichiers et les échanges précédents dont la requête a besoin. Il est affiché avant l’envoi.

{% hint style="danger" %}
Le mode cloud envoie des données hors de votre machine. C’est tout l’intérêt du mode cloud. Ce qu’affirme
VOOL porte sur le *trajet* — la requête va de votre ordinateur à votre fournisseur —, pas sur l’idée que
rien ne serait transmis.
{% endhint %}

## Ce qui se trouve entre les deux

Rien qui soit exploité par Parad0x Labs. Il n’y a ni proxy, ni passerelle, ni relais pour le trafic des
modèles. Votre clé est utilisée par l’app sur votre machine pour communiquer directement avec le
fournisseur.

Tous les détails : [Traitement des données](../trust/data-handling.md).

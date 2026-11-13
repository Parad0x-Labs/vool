---
description: Quelles données quittent votre ordinateur quand vous utilisez VOOL, et quand. La différence entre exécuter l’IA en local et utiliser un modèle cloud.
translation: machine
source_sha256: 2c2b9e26e99579c00a032dfeac95fd216fe9229497c965c1bc08aface7a6c071
---

# Local et cloud

VOOL peut répondre de deux façons : sur cet ordinateur, ou via un fournisseur cloud. L’en-tête de la
conversation indique de quelle façon la prochaine requête s’exécute — par exemple **Auto** avec
**Local en priorité** lorsque VOOL Auto répond depuis cet ordinateur.

## Local

Le modèle et vos fichiers restent sur cet ordinateur. Le modèle s’exécute sur un moteur local, les fichiers sont lus depuis le
disque, et aucune requête n’est envoyée à un fournisseur. Les informations en direct comme la météo
ou les prix viennent de services web publics uniquement lorsque Recherches web est activé (désactivé sur une nouvelle installation), et jamais en Local uniquement.

## Cloud (nom du fournisseur)

Le contexte sélectionné est envoyé directement au fournisseur que vous avez sélectionné, depuis votre
machine, avec votre clé.

« Le contexte sélectionné » désigne ce que l’app a assemblé pour cette requête : votre prompt, plus les
fichiers et les échanges précédents dont la requête a besoin.

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

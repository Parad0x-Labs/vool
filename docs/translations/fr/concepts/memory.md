---
description: Comment VOOL se souvient des choses d’une session à l’autre, et où se trouve cette mémoire.
translation: machine
source_sha256: 65af63e755076fe33675d7a5d6afa7f0c5664b77f716ffc0dc1f0f358e67f667
---

# Mémoire

La mémoire est la façon dont VOOL conserve des faits d’une session à l’autre. Elle est stockée sous forme
de fichiers sur votre machine, dans le dossier de données de VOOL, dans un format lisible.

## Sa qualité de mémoire

Sur 150 questions jamais utilisées du benchmark LoCoMo de longues conversations, la mémoire livrée avec
VOOL 0.7 a donné 123 bonnes réponses (82,0 %). Lors du face-à-face sur les mêmes questions, avec le même
modèle répondant pour les trois, VOOL a obtenu 124 (82,7 %), OpenClaw 107 (71,3 %) et Hermes 94
(62,7 %), tout en envoyant 3,5 à 5,4 fois moins de tokens par question. Chaque exécution est publiée :
[benchmarks de mémoire](https://github.com/Parad0x-Labs/vool/tree/main/docs/benchmarks).

## Propriétés

* **Sur le disque, en clair.** La mémoire est faite de fichiers que vous pouvez lire. `MEMORY.md` en est
  la face lisible ; les faits qui se trouvent derrière sont conservés dans un fichier d’entrées à côté.
* **Local.** La mémoire n’est pas synchronisée avec un serveur.
* **Un seul stockage par dossier de données VOOL.** La mémoire n’est pas cloisonnée par espace de
  travail — elle est conservée pour toute l’installation, et ne constitue donc pas une frontière
  d’isolation par projet.

## La gérer

Les notes que vous ajoutez vous-même à `MEMORY.md` sont préservées. Les faits que VOOL a enregistrés
s’effacent avec l’opération d’oubli de VOOL, pas en supprimant le fichier : l’oubli réécrit le stockage
des entrées et le miroir en une seule étape, puis relit les deux pour confirmer que le texte a disparu.
Supprimer seulement `MEMORY.md` n’est pas un effacement — le fichier d’entrées contient toujours les
faits, et le miroir est reconstruit à partir de lui.

En mode cloud, la mémoire pertinente peut faire partie du contexte envoyé au fournisseur, aux mêmes
conditions que tout autre contexte. Voir [Local et cloud](local-and-cloud.md).

Oublier dans une discussion efface aussi les mots de l’historique d’exécution de cette discussion dans la
base de données du runtime : les enregistrements des tours, les points de reprise, les tentatives, les
plans de tâches, les réponses finalisées (avec les copies que balaie l’effacement des réponses), les
connaissances candidates, les rapports de contexte, les notifications, les artefacts de recherche produits
par la discussion, et les messages épinglés qui les citaient. Les enregistrements dont dépendent d’autres
enregistrements restent, avec les mots oubliés masqués, ainsi que toute empreinte stockée à côté d’eux ;
les autres sont supprimés. Un tour qui était encore en cours quand l’oubli est arrivé ne peut pas réécrire
les mots : ses événements sont masqués et, quand il se termine, ses enregistrements sont de nouveau
effacés. Le journal d’écriture anticipée de la base de données est ensuite intégré à celle-ci, pour que
d’anciennes copies ne restent pas dans le fichier `-wal`. Les enregistrements d’une autre discussion ne
sont pas touchés. Un message en file d’attente qui n’a pas encore été envoyé est conservé, et l’oubli
indique alors qu’il n’a pas pu être entièrement appliqué. Une table qu’aucune étape d’effacement ne
connaît est nommée dans la réponse, qui précise qu’une nouvelle tentative ne la videra pas.

Les mots sont comparés sans tenir compte de la casse, et un chiffre écrit dans une autre écriture est ce
chiffre (un « １２ » en pleine chasse vaut 12). Un nombre oublié ne correspond que là où il se présente
comme un nombre à part entière : les lettres et les chiffres écrits accolés à lui ne peuvent contenir aucun
autre chiffre, et il ne peut pas se prolonger au-delà de `.`, `,`, `:`, `/`, `-` ou `_` vers un autre chiffre. Oublier « 12 » efface « code 12 », « 12kg » et « B12 »,
mais pas « chambre 312 », « 3.12 », « 12.5 », « 12:30 » ou « 2026-10-12 », et oublier « 200 » n’efface
pas « 1,200 ». Dans l’historique d’exécution, un nombre isolé que le runtime a compté ou chronométré (comme
une valeur `elapsed_ms` ou `step_count`) n’est pas pris en compte.

Les lecteurs de la mémoire vérifient aussi une autorité d’oubli durable. Un nouveau dossier de mémoire la
crée avant d’accepter des écritures. Un ancien stockage sémantique n’est mis à niveau sur place que si
aucun effacement antérieur n’est enregistré ; ses souvenirs conservés restent disponibles. Une autorité
existante mais illisible, une autorité manquante alors qu’un témoin persistant existe, ou une barrière
d’oubli enregistrée amène les lecteurs à retenir la mémoire concernée. Supprimer les fichiers d’autorité
ne permet pas de restaurer un contenu oublié.

Un journal d’effacement en attente conserve son jeton pour la récupération. Une fois que la révocation
permanente et délimitée existe, l’achèvement efface le jeton redondant du journal tout en conservant
l’identité et le statut de l’opération. L’autorité de révocation demeure.

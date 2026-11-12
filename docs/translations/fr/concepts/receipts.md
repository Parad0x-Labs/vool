---
description: Un reçu est l’enregistrement de ce qu’une exécution a réellement fait.
translation: machine
source_sha256: caffc8be79cb1017a81f74baa2451e076db2e6660302b57db4b9d3ec92dab8b8
---

# Reçus

Un reçu est l’enregistrement durable d’une exécution : quels outils ont été appelés, avec quelles
entrées, et ce que chacun a renvoyé.

## Pourquoi ils existent

Le résumé qu’un agent fait de son propre travail est une affirmation. Le reçu est l’enregistrement
auquel vous pouvez confronter cette affirmation.

## Ce qu’il contient

| Champ | Signification |
| --- | --- |
| `tool` | Quelle action s’est exécutée |
| `input` | Ce qui lui a été donné |
| `result` | Ce qu’elle a renvoyé |
| `time` | Quand elle s’est exécutée |

Les reçus sont conservés dans le « runtime home » de VOOL sur cet ordinateur (voir
[Configuration](../reference/configuration.md)), pas dans vos dossiers de projet. Un reçu ne quitte
votre machine qu’à l’intérieur d’un rapport de bug que vous prévisualisez et choisissez d’envoyer (voir
[Signalement de bugs et confidentialité](../trust/bug-reporting.md)).

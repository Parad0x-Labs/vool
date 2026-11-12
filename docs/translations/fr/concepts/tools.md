---
description: Les outils sont les actions que VOOL peut effectuer, et chacune est enregistrée.
translation: machine
source_sha256: 53722a7f30f4446b6d029465a402a823f60496a66008b5df150d201c1963b152
---

# Outils

Un outil est une action que le runtime peut effectuer en votre nom — lire un fichier, exécuter une
commande, chercher dans un dossier.

## Inspectable par conception

Chaque appel d’outil enregistre ce qui a été demandé, ce qui s’est exécuté et ce qui est revenu. Une
exécution peut être relue après coup sans être relancée.

## Permission

Qu’un outil s’exécute tout de suite ou demande d’abord dépend du mode que vous avez choisi et de
l’action. Une nouvelle discussion démarre en Manuel, où chaque modification demande d’abord. En Auto, la
création et la modification de fichiers, les commandes et les actions réseau s’exécutent sans demander,
tandis que la suppression, l’écrasement, l’installation, les commits et pushes Git, le déploiement,
l’envoi de messages, les paiements et la modification des réglages demandent toujours. Dans tous les
modes, les outils de fichiers ne fonctionnent qu’à l’intérieur de l’[espace de travail](workspaces.md),
les outils de recherche et de récupération lisent des ressources publiques sans rien écrire, et les
secrets enregistrés ne sont jamais lus. [Permissions](permissions.md) couvre les cinq modes et ce que
permet chaque bouton d’approbation.

```text
tool: read_file
path: src/main.py
result: 412 lines
```

Voir aussi [Reçus](receipts.md).

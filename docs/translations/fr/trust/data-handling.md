---
description: Exactement quelles données VOOL envoie, quand et à qui. Ce qui reste sur votre ordinateur et ce qui parvient à votre fournisseur d’IA.
translation: machine
source_sha256: ec76a421c8153c33d699b77f7b069d9c2708eb129ba5ceaeb088fd75d60f41f0
---

# Traitement des données

## Les deux états

**LOCAL** — tout reste sur cet ordinateur.

**CLOUD** — le contexte sélectionné est envoyé directement au fournisseur sélectionné.

L’indicateur de l’interface montre quel état est actif avant que vous n’envoyiez quoi que ce soit.

## Ce que VOOL ne fait pas

* Il ne fait pas transiter vos prompts par l’infrastructure de Parad0x Labs.
* Il n’envoie pas votre espace de travail en ligne.
* Il n’envoie votre clé nulle part, sauf au fournisseur auquel elle appartient.

## OpenRouter en particulier

Votre compte OpenRouter reste le vôtre. VOOL envoie les requêtes directement depuis cet ordinateur
avec votre clé. Parad0x ne relaie pas vos prompts et ne paie pas votre utilisation. L’utilisation réelle de VOOL
peut apparaître dans les statistiques OpenRouter publiques et agrégées de VOOL, mais les prompts individuels
et les identités ne sont pas publiés.

## Télémétrie

VOOL n’envoie aucune télémétrie produit. Un rapport de bug ne quitte votre machine que lorsque vous l’avez
prévisualisé et que vous choisissez de l’envoyer ; voir [Signalement de bugs et confidentialité](../../../trust/bug-reporting.md).

## Les conditions de votre fournisseur s’appliquent

En mode cloud, vos données parviennent à votre fournisseur selon **ses** conditions de conservation et
d’entraînement, pas celles de VOOL. Lisez les conditions du fournisseur que vous sélectionnez.

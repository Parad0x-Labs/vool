---
description: Exactement quelles données VOOL envoie, quand et à qui. Ce qui reste sur votre ordinateur et ce qui parvient à votre fournisseur d’IA.
translation: machine
source_sha256: 4fd0aaa4d2480ae80b94aa7132c46c8263113450e4b02c5747388791e7bc8710
---

# Traitement des données

## Les deux états

**Local** — le modèle et vos fichiers restent sur cet ordinateur. Les informations en direct comme
la météo ou les prix viennent de services web publics uniquement lorsque Recherches web est activé (désactivé sur une nouvelle installation), et jamais en Local uniquement.

**Cloud** — le contexte sélectionné est envoyé directement au fournisseur sélectionné.

L’en-tête de la conversation indique de quelle façon la prochaine requête s’exécute — par exemple **Auto** avec
**Local en priorité** lorsque VOOL Auto répond depuis cet ordinateur.

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
prévisualisé et que vous choisissez de l’envoyer ; voir [Signalement de bugs et confidentialité](bug-reporting.md).

## Les conditions de votre fournisseur s’appliquent

En mode cloud, vos données parviennent à votre fournisseur selon **ses** conditions de conservation et
d’entraînement, pas celles de VOOL. Lisez les conditions du fournisseur que vous sélectionnez.

---
description: Comment lire une erreur de VOOL, savoir si quelque chose s’est déjà produit, et où chaque code est expliqué.
translation: machine
source_sha256: ccb01d4c778254a055fd67f46241c670aac90cfc83f168b923a19cef1f949de2
---

# Erreurs et codes d’erreur

Quand quelque chose échoue, VOOL dit en mots simples ce qui s’est passé, ce qui a peut-être déjà eu lieu
et ce qu’il faut faire ensuite. Chaque échec a aussi un code stable. Vous le trouvez dans les détails
techniques sous une erreur, dans la ligne de commande (`wallet_export_refused: …`) et dans les réponses
de l’API. Cette page explique comment lire une erreur. Le [Livre des erreurs](../ERROR_BOOK.md) explique
chaque code.

## Lire une erreur

Vérifiez trois choses avant de réessayer :

1. **Ce qui s’est passé.** Le message. Il ne contient jamais de chemins internes ni de secrets.
2. **Ce qui a peut-être déjà eu lieu.** La plupart des refus interviennent avant que quoi que ce soit soit
   envoyé ou modifié, et le message le dit. Certains échecs ne peuvent pas savoir si une action s’est
   terminée, comme un délai dépassé après le départ d’une requête. Le message dit alors que le résultat
   est inconnu et vous demande de vérifier avant de réessayer. Un paiement n’est jamais envoyé deux fois
   par accident : un second envoi du même paiement est refusé avec
   [`wallet_duplicate_payment`](../ERROR_BOOK.md#wallet-duplicate-payment).
3. **Ce qu’il faut faire ensuite.** Une étape concrète.

Le code porte le sens. Un statut HTTP comme 403 ou 429 dit seulement comment une requête s’est passée en
chemin, et deux refus différents peuvent partager un même statut. Lisez le code, pas le statut.

## Les codes et le Livre des erreurs

Un code n’est jamais renommé ni réutilisé pour un autre sens. Le
[Livre des erreurs](../ERROR_BOOK.md) est généré à partir du catalogue d’erreurs de l’app elle-même,
donc les codes, les messages et les étapes suivantes qu’il contient sont exactement ce que l’app affiche.
Chaque code a sa propre section : cherchez dans la page le code tel qu’on vous l’a donné, ou faites un
lien vers la section avec l’adresse du livre, `#` et le code avec les tirets bas remplacés par des
tirets, comme [`wallet_broadcast_failed`](../ERROR_BOOK.md#wallet-broadcast-failed).

Les codes que vous verrez le plus probablement :

| Code | Titre | Ce qui a peut-être déjà eu lieu |
| --- | --- | --- |
| [`permission_denied`](../ERROR_BOOK.md#permission-denied) | Permission refusée | Rien n’a été modifié. |
| [`provider_unavailable`](../ERROR_BOOK.md#provider-unavailable) | Fournisseur injoignable | Rien n’a été envoyé. |
| [`provider_exhausted`](../ERROR_BOOK.md#provider-exhausted) | Limite du fournisseur atteinte | La requête a atteint le fournisseur. |
| [`timeout`](../ERROR_BOOK.md#timeout) | Délai dépassé | Inconnu : vérifiez avant de réessayer. |
| [`cancelled`](../ERROR_BOOK.md#cancelled) | Annulé | Inconnu : une partie a peut-être déjà été exécutée. |
| [`credential_failure`](../ERROR_BOOK.md#credential-failure) | L’identifiant stocké n’a pas pu être lu | Rien n’a été envoyé ni modifié. |
| [`unknown`](../ERROR_BOOK.md#unknown) | Erreur inattendue | Inconnu : vérifiez avant de réessayer. |
| [`wallet_quote_mismatch`](../ERROR_BOOK.md#wallet-quote-mismatch) | L’approbation ne correspond plus à l’aperçu | Rien n’a été signé ni envoyé. |
| [`wallet_broadcast_failed`](../ERROR_BOOK.md#wallet-broadcast-failed) | Le paiement n’a pas pu être diffusé | Inconnu : vérifiez le statut du paiement avant de réessayer. |
| [`wallet_duplicate_payment`](../ERROR_BOOK.md#wallet-duplicate-payment) | Paiement déjà proposé ou envoyé | Rien de nouveau n’a été envoyé ; le paiement précédent est enregistré. |

## Erreurs et autres résultats

VOOL distingue ces cas, et sa formulation aussi :

* **Une erreur.** Quelque chose a échoué. Le code et le message disent quoi, et ce qu’il faut faire.
* **En attente de vous.** Une demande de permission ou une approbation de paiement est ouverte. Rien ne
  s’est exécuté, et rien ne s’exécutera tant que vous n’aurez pas répondu. Voir
  [Permissions](../concepts/permissions.md).
* **Arrêté par un plafond de dépenses ou un garde-fou de prix.** Le refus a lieu avant que le
  fournisseur soit contacté, et le message ne laisse jamais entendre que le fournisseur a été sollicité.
  Voir [Limites de dépenses et garde-fous de prix](../guides/spending-limits.md).
* **Annulé.** Vous ou VOOL l’avez arrêté avant la fin. Une partie a peut-être déjà été exécutée, donc
  vérifiez ce qui a changé avant de le relancer.
* **Impossible ici.** Cette version ou cette machine ne peut pas le faire, et le message indique ce qui
  le peut : par exemple pour une offre de paiement dont VOOL ne peut pas vérifier le schéma de bout en
  bout, ou pour un complément facultatif qui n’est pas installé.
* **Résultat inconnu.** L’action s’est peut-être terminée, peut-être pas, comme un paiement dont la
  diffusion n’a jamais été confirmée. Vérifiez d’abord son statut. VOOL ne paie jamais une seconde fois à
  l’aveugle.

## Erreurs avec leurs propres codes

Quelques parties de VOOL expliquent leurs échecs avec leurs propres codes courts, sur l’écran où l’échec
se produit, et ne figurent pas dans le Livre des erreurs : les pièces jointes (par exemple un fichier trop
volumineux, ou un identifiant trouvé dans un fichier collé), les mises à jour de l’app, les dossiers de
projet, le choix du modèle, les plafonds de dépenses, les contacts, les plugins et les compétences, la
saisie vocale, les données enregistrées et les rapports de bug. Leurs messages disent de la même façon
ce qui s’est passé et ce qu’il faut faire. Voir
[Discussions et pièces jointes](../concepts/chats-and-attachments.md),
[Mises à jour et migration](updates-migration.md) et
[Signalement de bugs et confidentialité](../trust/bug-reporting.md).

## Un code que vous ne reconnaissez pas

Un code qui ne figure pas dans le Livre des erreurs ne vient pas de cette version de VOOL. Vérifiez
quelle version l’a affiché, puis cherchez dans le [Livre des erreurs](../ERROR_BOOK.md) de cette version.
S’il vient bien de cette version, signalez-le avec le code exactement tel qu’il est écrit, et avec la
référence (`fault-` et douze caractères) quand la réponse en contenait une. Un échec que VOOL ne peut pas
classer est affiché comme [`unknown`](../ERROR_BOOK.md#unknown), jamais comme une cause devinée.
Voir [Signalement de bugs et confidentialité](../trust/bug-reporting.md).

## Résoudre des symptômes précis

Voir [Dépannage](../help/troubleshooting.md) pour les problèmes d’installation, de modèle et de
plateforme, et [Sauvegarde et restauration](../help/backup-and-recovery.md) pour récupérer vos
discussions, votre mémoire et votre portefeuille.

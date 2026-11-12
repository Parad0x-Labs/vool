---
description: Limites de dépenses, garde-fous de prix, et ce qui se passe quand un appel payant est interrompu.
translation: machine
source_sha256: a4a5246059942702330cbfc18e9872dc5b0f46192fa5b852bfc9060ca987eae6
---

# Limites de dépenses et garde-fous de prix

VOOL ne dépense jamais d’argent en votre nom sans un plafond explicite que vous contrôlez. Cette page
décrit les niveaux qui protègent les appels de modèle payants et les ressources payantes, et ce qui se
passe quand un appel payant est interrompu en cours de route.

## Deux prix, une règle

* **Prix du catalogue** — ce qu’un fournisseur affiche actuellement pour un modèle. VOOL le découvre ;
  c’est une information, pas une permission.
* **Votre prix accepté** — le plafond que vous avez approuvé pour ce fournisseur et ce modèle dans les
  Réglages. Un prix découvert au-dessus de votre plafond est refusé **avant** que le fournisseur soit
  contacté. Le refus dit exactement cela : le modèle ne s’est pas exécuté, rien n’a été envoyé, rien n’a
  été facturé.

## Plafonds de dépenses

Pour les appels payants, VOOL réserve de l’argent sur les plafonds avant chaque appel :

| Plafond | Signification |
| --- | --- |
| Par appel | Le plafond du coût prévu d’un seul appel. |
| Par tâche | Le maximum qu’une tâche peut dépenser sur l’ensemble de ses appels. |
| Quotidien / mensuel | Des plafonds sur toute la période pour l’utilisation payante. |

Un appel qui dépasserait un plafond est refusé avant d’être envoyé. Le refus nomme le plafond qui l’a
refusé (`per_task_spend_cap_exceeded` et ses semblables) et la formulation se place délibérément avant
l’envoi : *« running it would exceed your spend cap »* — jamais une affirmation que le fournisseur a été
contacté.

## Un modèle fixé n’est jamais remplacé en silence

Si vous choisissez explicitement un modèle payant et qu’un garde-fou le refuse, VOOL ne répond **pas**
discrètement avec un modèle moins cher. Le tour échoue en nommant le modèle que vous avez choisi et le
contrôle réellement en cause, avec la phrase *« no other model was substituted. »* Choisissez vous-même
un autre modèle, ou relevez délibérément le plafond.

## Payé, mais la réponse n’est jamais arrivée

Si un appel payant a été facturé mais que sa réponse n’est pas arrivée, le résultat est **inconnu** —
VOOL ne prétendra pas qu’il a échoué ou réussi :

* Aucune réponse n’est inventée à sa place, et aucun remboursement n’est supposé.
* Le paiement reste enregistré comme non résolu.
* Les Réglages le listent sous **Paid calls waiting for an answer**, où **Resume** demande une seule fois
  la même réponse, **sans payer de nouveau**.

C’est pourquoi une nouvelle tentative à l’aveugle n’est jamais suggérée dans ces cas : un simple renvoi
pourrait payer deux fois. Resume est la seule voie prise en charge.

## Paiements automatiques du portefeuille (x402)

Les sites web qui demandent un paiement x402 en USDC sur Base ou Base Sepolia peuvent être payés depuis
votre portefeuille Pilote Crypto, avec une approbation par PIN dans les limites de ces plafonds ; aucun
paiement réel sur le mainnet n’a encore été effectué ainsi. L’option de paiement à l’appel de la place de
marché des modèles, dans les Réglages, paie depuis le portefeuille Solana Mainnet, avec une approbation
par appel dans les limites de ces plafonds, et n’a pas encore été testée sur une vraie chaîne.
Les ressources payées par le portefeuille (voir [Portefeuille](../guides/wallet.md)) ont leur propre
plafond de paiement automatique. Une ressource qui demande plus que ce plafond n’est tout simplement pas
payée ; vous pouvez relever délibérément le plafond ou payer la ressource par une proposition explicite
avec votre approbation.

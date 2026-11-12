---
description: Ce que l’assistant IA VOOL vous demande la première fois que vous l’ouvrez, et ce qu’il ne fait pas.
translation: machine
source_sha256: cedf20410d47a2d277e5522bb3e55169854a91a267d71193a762e072e7f8e47e
---

# Premier lancement

Le premier lancement pose quatre questions courtes. Vous pouvez passer chacune d’elles et modifier toutes vos
réponses plus tard. Rien n’est envoyé nulle part pendant que vous répondez, sauf la vérification d’une clé cloud
auprès de son fournisseur si vous en ajoutez une.

## Ce qui se passe

1. **Où doit-il penser ?** Sur cet ordinateur avec un modèle local, ou en ligne avec une clé d’un
   fournisseur que vous payez. Voir [Connecter un modèle](connect-a-model.md).
2. **Choisissez un dossier.** Le dossier de projet dans lequel VOOL travaille. Voir
   [Espaces de travail](../../../concepts/workspaces.md).
3. **Que peut-il faire seul ?** À quelle fréquence VOOL s’arrête pour vous demander avant de modifier quoi que
   ce soit. Voir [Outils](../../../concepts/tools.md).
4. **Comment doit-il vous appeler ?** Facultatif. Un prénom ou un surnom suffit, et il reste sur
   cet ordinateur.

L’indicateur de mode affiche ensuite Local, Auto ou le nom du fournisseur cloud, et reflète le modèle
actuellement sélectionné.

**Certifiez une fois un modèle local.** Avant qu’un modèle local puisse répondre, ouvrez
**Réglages → Modèles et fournisseurs → Modèles locaux** et appuyez sur **Certify** pour ce modèle. Cela lance
une courte vérification sur votre machine.

## Ce que VOOL ne fait pas au premier lancement

* Il ne crée pas de compte.
* Il n’exige pas de clé cloud. Un modèle local suffit pour utiliser l’app.
* Il n’indexe pas votre disque. Il ne lit des fichiers que lorsqu’une demande en a besoin ; voir
  [Espaces de travail](../../../concepts/workspaces.md) pour les dossiers auxquels il peut accéder.

{% hint style="info" %}
Vous pouvez choisir un autre dossier de projet à tout moment avec **+ Projet** dans la barre latérale de discussion.
{% endhint %}

## Suite

Continuez avec [Connecter un modèle](connect-a-model.md).

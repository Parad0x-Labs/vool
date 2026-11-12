---
description: Le portefeuille Pilote Crypto, facultatif — réseaux, paiements x402, frais DNA et récupération, tels que cette version les livre réellement.
translation: machine
source_sha256: 46121a5ed26be175ddd3c66b269df356e074adbb57c8e9806461b80197613cc2
---

# Portefeuille

Le portefeuille est **facultatif et désactivé par défaut**. Il existe pour que VOOL puisse détenir une
petite somme en crypto pour les services d’IA qui facturent à la requête. Rien de tout cela n’est
nécessaire pour utiliser VOOL.

Cette page décrit ce que la version actuelle prend réellement en charge. Le portefeuille est en phase
**pilote** : les capacités sont ajoutées voie par voie, et chaque voie ci-dessous dit exactement ce
qu’elle fait.

## Environnements réseau

Quand Crypto est activé, VOOL fonctionne sur le **Mainnet** : Solana, Robinhood Chain, Base, Ethereum,
BNB et Arbitrum One.
La configuration normale ne vous demande jamais de choisir un environnement réseau.

* **Mainnet** — la valeur par défaut de chaque installation, y compris les installations mises à niveau
  qui ont d’anciens comptes de réseau de test. Chaque plafond de dépenses, approbation et reçu s’applique
  comme décrit ci-dessous.
* **Test networks** — Solana Devnet, Robinhood Chain Testnet et les autres voies de test. Réservé aux
  développeurs : changez dans **Réglages → Crypto → Developer options**.

Les comptes, les soldes et les reçus appartiennent à un seul environnement et ne passent jamais de l’un à
l’autre ; les comptes de l’autre environnement restent listés sur leur propre réseau. Une demande pour un
réseau de l’autre environnement est refusée sans que rien soit créé, proposé ni signé.

{% hint style="warning" %}
Une documentation antérieure disait que le mainnet était impossible dans cette version. C’était vrai
avant le pilote et ne l’est plus : des lignes mainnet existent désormais. Ce qui reste vrai est plus
restreint — voyez les sections sur les transferts et les paiements ci-dessous pour savoir exactement
quelles voies existent sur le mainnet.
{% endhint %}

## Ce qui circule sur quelle voie

* **Les transferts déplacent des coins natifs** (par exemple SOL sur Solana, ETH sur Base, Ethereum ou
  Arbitrum One), et **de l’USDT sur Solana, Ethereum, BNB Smart Chain et Arbitrum One**.
  La chaîne fait partie de chaque adresse de contact enregistrée et de chaque approbation. Les transferts
  de jetons ont aussi besoin du coin natif de cette chaîne pour les frais ; la cotation affiche les deux
  montants dans vos limites.
* **L’USDC sur Solana et Base n’est disponible que pour les canaux de paiement.** Il n’est pas proposé
  pour les envois ordinaires. L’affichage principal du solde du portefeuille montre les coins natifs, pas
  un portefeuille de jetons complet.
* Chaque transfert est présenté d’avance (une **cotation** : montant, plafond de frais, destinataire,
  réseau) et exige votre approbation explicite liée exactement à cet aperçu. Changez quoi que ce soit et
  l’approbation est annulée — rien n’est signé.
* Un nom qui correspond à plusieurs contacts ou adresses de portefeuille vous demande de choisir.
  Vérifiez l’adresse et la chaîne exactes. Une adresse modifiée ou un contact renommé invalide l’ancien
  examen.
* Sur le mainnet, seul un portefeuille VOOL créé sur cet appareil envoie. Un portefeuille externe que
  vous avez connecté ou une adresse en lecture seule peut recevoir et afficher son solde ; un envoi ou un
  paiement par canal de paiement depuis celui-ci est refusé avant qu’une approbation soit demandée, et
  rien n’est signé.
* « Activité » enregistre les paiements effectués via VOOL et leurs reçus. Ce n’est pas un historique
  complet des paiements entrants. Ce portefeuille ne propose aucune action d’échange (swap) ni de pont
  (bridge).

## Comment un paiement est approuvé

1. VOOL prépare une proposition avec les montants exacts et un plafond de frais.
2. Vous l’approuvez avec votre PIN/mot de passe (ou la présence sur l’appareil, là où elle est
   disponible).
3. Le paiement est signé et envoyé, et un reçu est enregistré.

Les approbations ne se transfèrent pas d’une proposition à l’autre. Un mauvais PIN ne signe rien. Trop de
tentatives erronées verrouillent le portefeuille pendant un moment — délibérément, avec une attente
indiquée.

## Paiements automatiques x402

x402 est un protocole de paiement à la requête que parlent certains sites web et services d’IA. **Les
sites web qui demandent un paiement x402 en USDC sur Base** (ou Base Sepolia, dans les Developer options)
peuvent être payés depuis votre portefeuille Pilote Crypto : une approbation avec votre PIN, dans vos
limites. Le service de paiement du site web prend en charge les frais de réseau. **Aucun paiement réel
sur le mainnet n’a encore été effectué ainsi.** Les autres réseaux de test (Solana Devnet, Ethereum
Sepolia et BNB testnet) restent dans les Developer options. **Réglages → Crypto → Payment channels**
liste chaque façon dont le portefeuille peut payer un site web, chacune avec son propre interrupteur ; un
canal désactivé refuse avant que quoi que ce soit soit signé. Le paiement via Solana pay-kit n’est pas
inclus dans les téléchargements, donc ce canal apparaît comme inutilisable. **Place de marché des
modèles :** l’option de paiement à l’appel de la place de marché des modèles, dans les Réglages, peut
payer depuis le portefeuille Pilote Crypto Solana Mainnet en USDC ou en SOL. Chaque appel attend votre
approbation dans les limites de votre portefeuille et affiche les frais VOOL de 0,1 %. Ce chemin n’a pas
encore été testé sur une vraie chaîne.
Quand une ressource payante utilise x402 :

* Les paiements passent par les mêmes plafonds du portefeuille et le même processus d’approbation.
* Les paiements automatiques sont plafonnés par paiement ; une ressource au-dessus du plafond n’est
  **pas payée** (vous pouvez relever délibérément le plafond ou la payer par une proposition explicite).
* Si un paiement n’a pas pu aboutir, le résultat est signalé comme **inconnu** — vérifiez son statut
  avant de réessayer ; VOOL n’invitera pas à un double paiement à l’aveugle.

## Frais de service DNA

La voie x402 de paiement à l’appel de la place de marché des modèles prélève des frais de service de
**0,1 % (10 points de base)** sur chaque paiement accepté à un fournisseur, affichés séparément du
paiement et séparément des frais de réseau. Rien d’autre n’y donne lieu : les envois ordinaires, la
création d’un portefeuille, l’inférence locale et vos propres appels avec votre clé API (BYOK) ne
comportent **pas** de frais de service. Les frais accumulés sont perçus de façon économique (avec un
paiement natif ultérieur, sous une seule approbation, uniquement quand le coût de la perception est
limité) — jamais par un prélèvement séparé inattendu.

## Récupération

* La **phrase de récupération est affichée exactement une fois**, à la création.
* La clé de sauvegarde d’un portefeuille pilote n’est, elle aussi, révélée qu’une fois pendant la
  configuration, et il n’existe pas d’export ultérieur. Si vous ne l’avez pas enregistrée, annulez la
  configuration et n’envoyez pas de fonds à cette adresse.
* Un ancien portefeuille VOOL, créé avant Pilote Crypto, peut exporter sa clé privée une fois pour
  l’importer dans une autre app de portefeuille. L’export ne s’ouvre qu’avec l’identification propre de
  votre Mac (Touch ID ou le mot de passe de votre Mac), jamais avec le PIN du portefeuille, et la clé est
  affichée une fois sans être stockée.
* Vous avez perdu un portefeuille ? Récupérez-le à partir de la phrase que vous avez notée à la création.
  Une preuve de récupération qui ne correspond pas exactement à ce portefeuille est refusée avant toute
  écriture.

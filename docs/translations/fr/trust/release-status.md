---
description: Où en est VOOL, dit simplement.
translation: machine
source_sha256: bf79dfc262c1aca9c489c8474ad2de5fa633b0b60a515f2835f52954c66ad73f
---

# État des versions

Cette page est la référence tenue à jour sur l’état des versions. D’autres documents mentionnent des
faits sur les versions en passant ; quand ils contredisent cette page, c’est cette page qui fait foi.

## Étape actuelle

VOOL est en **bêta**. La version actuelle est
[**v0.7.7-beta**](https://github.com/Parad0x-Labs/vool/releases/tag/v0.7.7-beta), une version GitHub
construite à partir de `main` au commit `8af687c`. Elle propose des téléchargements de l’app pour trois
plateformes, chacun avec une somme de contrôle SHA-256 publiée. L’installation depuis les sources avec
le script d’amorçage reste prise en charge sur macOS, Linux et Windows ; voir
[Installation](../getting-started/install.md).

| Téléchargement | Pour |
| --- | --- |
| `VOOL-0.7.7-beta-macos-arm64.dmg` | Mac Apple Silicon, macOS 14 ou plus récent |
| `VOOL-0.7.7-beta-macos-x86_64.dmg` | Mac Intel, macOS 14 ou plus récent |
| `vool-0.7.7-beta-linux-x86_64.tar.gz` | Linux x86_64, testé sur Ubuntu 22.04 et 24.04 |

Les enregistrements de versions de ce dépôt couvrent le canal GitHub. Les autres canaux de
téléchargement sont exploités séparément ; vérifiez tout téléchargement avec la somme de contrôle publiée
sur la version GitHub.

| | Statut |
| --- | --- |
| Téléchargements publics | DMG macOS Apple Silicon, DMG macOS Intel, archive tar Linux x86_64 sur la version GitHub v0.7.7-beta |
| Sommes de contrôle publiées | SHA-256 pour chaque téléchargement, sur la version |
| Signature du code | Les apps macOS sont signées ad hoc, pas avec un Developer ID. La signature sera ajoutée après cette bêta et n’en est pas une condition |
| Notarisation macOS | Pas notarisée. macOS vous demande de confirmer la première ouverture (voir ci-dessous) |
| Windows | Pas de téléchargement Windows dans cette version ; sous Windows, VOOL s’installe depuis les sources et reste expérimental |
| Mises à jour | À partir de 0.7.5, VOOL se met à jour tout seul : avec « Se connecter de lui-même » activé, il affiche un avis et installe une nouvelle version en un clic. Mise à jour depuis 0.7.4 ou 0.7.3 sur un Mac : activez « Se connecter de lui-même » (Réglages > Confidentialité et permissions) et appuyez une fois sur « Rechercher des mises à jour ». Sous Linux, ou depuis 0.7.2, 0.7.1 ou 0.7.0 : téléchargez 0.7.7 et installez-le par-dessus votre version actuelle. Vos discussions et vos réglages se trouvent dans votre profil, en dehors de l’app. Voir [Mises à jour et migration](../reference/updates-migration.md) |
| Stabilité de l’API | Change d’une version à l’autre |

## Ce que cela signifie pour vous

* Au premier lancement d’une app Mac, macOS refuse de l’ouvrir parce qu’elle n’est pas notarisée. C’est
  attendu à ce stade, pas le signe d’un téléchargement endommagé. Vérifiez la somme de contrôle, puis
  ouvrez **Réglages Système → Confidentialité et sécurité** et cliquez sur **Ouvrir quand même**. Sous
  macOS 14, vous pouvez à la place cliquer sur l’app en maintenant la touche Contrôle enfoncée et choisir
  **Ouvrir**.
* Choisissez le téléchargement Mac qui correspond à votre puce. L’app Apple Silicon ne fonctionne pas sur
  un Mac Intel et le signale au lancement.
* Les formats de configuration peuvent changer d’une version à l’autre.
* Les fonctions crypto sont désactivées jusqu’à ce que vous les activiez.

## Limitations de la bêta et du pilote, énoncées explicitement

| Fonction | État dans cette version |
| --- | --- |
| Crypto et portefeuille | Désactivés par défaut. Quand vous les activez, VOOL utilise le mainnet ; les réseaux de test se trouvent dans les Developer options. Chaque paiement passe par le portefeuille de VOOL, avec ses limites, votre approbation et un reçu. Voir [Portefeuille](../guides/wallet.md). |
| Transferts sur le mainnet | Les coins natifs, et l’USDT sur Solana, Ethereum, BNB Smart Chain et Arbitrum One, envoyés depuis un portefeuille VOOL créé sur cet appareil. L’USDC sur Solana et Base sert uniquement aux canaux de paiement. Un compte connecté ou en lecture seule peut recevoir et afficher son solde, mais ne peut pas envoyer depuis VOOL. |
| Payer avec x402 | Les sites web qui demandent un paiement x402 en USDC sur Base ou Base Sepolia peuvent être payés depuis votre portefeuille Pilote Crypto : une approbation avec votre PIN, dans vos limites. Le service de paiement du site web prend en charge les frais de réseau. **Réglages → Crypto → Payment channels** a un interrupteur pour chaque façon dont le portefeuille peut payer un site web ; un canal désactivé refuse avant que quoi que ce soit soit signé. Aucun paiement réel sur le mainnet n’a encore été effectué ainsi. Le paiement via Solana pay-kit n’est pas inclus dans les téléchargements ; ce canal apparaît comme inutilisable. |
| X | Depuis n’importe quelle discussion : rechercher des publications et des comptes, des Spaces, des tendances et des DM, et consulter les anciens calls sur un token. Tout ce qui agit sur votre compte attend votre accord. |
| Dictée | Temporairement indisponible dans cette version bêta ; elle reviendra dans une mise à jour. |
| Mode de question cloud « rafale » | La demande de confirmation par appel n’est pas encore branchée dans cette version. |
| Modèles locaux | Avant qu’un modèle local puisse répondre, ouvrez **Réglages → Modèles et fournisseurs → Modèles locaux** et appuyez une fois sur **Certify** pour ce modèle. |
| Mac Intel | Les modèles locaux tournent sur le processeur et sont lents. La certification tient compte des processeurs plus lents, mais une longue réponse de mémoire peut dépasser le délai, donc utilisez pour l’instant un modèle cloud sur ces Mac. |
| Discussion privée | Dites « discussion privée », « n’enregistrez pas ça » ou « ça reste entre nous », et rien de cette discussion n’est enregistré. Les commandes fonctionnent dans les 17 langues de l’app. |
| Oublier | Oublier efface la mémoire de VOOL et l’historique d’exécution correspondant, pour que l’élément ne soit plus rappelé ni listé, et efface les mots oubliés du fichier de mémoire local de VOOL et de son index de recherche. VOOL garde les mots que vous lui avez demandé d’oublier dans une courte liste sur votre ordinateur, pour pouvoir continuer à les refuser. Après que vous avez fait oublier quelque chose dans une discussion, VOOL ne reprend pas ce même fait dans cette discussion ; commencez une nouvelle discussion pour le lui redire. |
| PDF numérisés | Lus avec la reconnaissance de texte intégrée du Mac, donc sur Mac uniquement. |
| Animal de bureau | L’animal vit dans la fenêtre de VOOL. Le faire glisser hors de la fenêtre ne le déplace pas sur le bureau. |
| Codex des compétences | « Parcourir le codex des compétences » montre les compétences listées pour votre version exacte de VOOL, et vous examinez une compétence avant qu’elle s’installe. « Publier dans le codex des compétences » vérifie une compétence que vous avez créée sur votre ordinateur, puis ouvre le site du codex des compétences avec la compétence préremplie ; vous vous y connectez et appuyez sur Send, et VOOL n’envoie rien lui-même. Seul le texte de la compétence est transmis, donc une compétence avec des fichiers supplémentaires ne peut pas encore être partagée. |

La signature Developer ID et la notarisation pour macOS, ainsi que des sommes de contrôle signées pour
Linux, sont les prochaines étapes sur le chemin des versions ; ni l’une ni l’autre n’est revendiquée
aujourd’hui.

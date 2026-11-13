---
translation: machine
source_sha256: 5a872f9502c66828af4dfb1e7e687226facc2aaad8cb6544054c90e1fafe8d7d
---

# Livre des erreurs de VOOL

<!-- GENERATED FILE — do not hand-edit. Regenerate with `python -m tools.generate_error_book`. -->

<!-- GENERATED FILE: the app's own texts for each code, and the words in tools/docs/error_book_words.json. Regenerate with tools/docs/translate_error_book.py, never by hand. -->

Toutes les erreurs que VOOL peut vous afficher, générées à partir du catalogue d’erreurs de l’app elle-même : les codes, messages et étapes suivantes ci-dessous sont exactement ce que l’app affiche.

- **Schéma :** `vool.fault.v1`
- **Version du catalogue :** `1`
- **Condensé du catalogue :** `2d2c27206b99b26ca524806ec42ac41eac2f0d28a110a74f6ac5ef42006f313d`
- **Nombre de défauts :** 59

Chaque code est stable et n’est jamais réutilisé avec un autre sens. Chaque code a sa propre section ci-dessous, dont le titre est le code lui-même ; en cherchant sur cette page le code tel qu’on vous l’a donné, vous trouvez donc sa section, et un lien vers la section est l’adresse de cette page suivie de `#` et du code avec des traits d’union à la place des traits de soulignement. Chaque section indique ce qui s’est passé, ce qui a peut-être déjà eu lieu, ce qu’il faut faire ensuite et si un nouvel essai peut aider. Si un code qu’on vous a donné ne figure pas dans ce livre, il ne vient pas de cette version de VOOL : vérifiez quelle version l’a affiché, ou signalez-le avec le code tel qu’il est écrit.

## Sécurité

| Code | Titre | Gravité | Ce qui a peut-être déjà eu lieu |
|---|---|---|---|
| [`confinement_refusal`](#confinement-refusal) | Modification hors du périmètre autorisé | élevée | Rien n’a été envoyé, signé ni modifié |
| [`credential_failure`](#credential-failure) | L’identifiant stocké n’a pas pu être lu | élevée | Rien n’a été envoyé, signé ni modifié |
| [`wallet_backup_unavailable`](#wallet-backup-unavailable) | Sauvegarde déjà montrée une fois | élevée | Rien n’a été envoyé, signé ni modifié |
| [`wallet_caller_refused`](#wallet-caller-refused) | Non lancé depuis la fenêtre propre à VOOL | élevée | Rien n’a été envoyé, signé ni modifié |
| [`wallet_card_data_refused`](#wallet-card-data-refused) | Données de carte refusées | élevée | Rien n’a été envoyé, signé ni modifié |
| [`wallet_chain_identity_mismatch`](#wallet-chain-identity-mismatch) | Identité du point de terminaison de la chaîne non prouvée | élevée | Une requête a atteint un service externe ; aucun paiement n’a été effectué |
| [`wallet_environment_inactive`](#wallet-environment-inactive) | Mauvais environnement réseau | moyenne | Rien n’a été envoyé, signé ni modifié |
| [`wallet_export_pin_not_accepted`](#wallet-export-pin-not-accepted) | L’export ne se déverrouille que par l’authentification de l’appareil | élevée | Rien n’a été envoyé, signé ni modifié |
| [`wallet_export_refused`](#wallet-export-refused) | Ancienne façon d’exporter retirée | élevée | Rien n’a été envoyé, signé ni modifié |
| [`wallet_export_unavailable`](#wallet-export-unavailable) | Export indisponible pour ce portefeuille | élevée | Rien n’a été envoyé, signé ni modifié |
| [`wallet_legacy_surface_retired`](#wallet-legacy-surface-retired) | Chemin d’argent hérité retiré | élevée | Rien n’a été envoyé, signé ni modifié |
| [`wallet_network_disabled`](#wallet-network-disabled) | Réseau non déclaré pour ce portefeuille | élevée | Rien n’a été envoyé, signé ni modifié |
| [`wallet_outbound_refused`](#wallet-outbound-refused) | Requête hors des points de terminaison approuvés | élevée | Rien n’a été envoyé, signé ni modifié |
| [`wallet_quote_mismatch`](#wallet-quote-mismatch) | L’approbation ne correspond plus à l’aperçu | élevée | Rien n’a été envoyé, signé ni modifié |
| [`wallet_recovery_refused`](#wallet-recovery-refused) | La preuve de récupération ne correspond pas à ce portefeuille | élevée | Rien n’a été envoyé, signé ni modifié |
| [`wallet_signature_invalid`](#wallet-signature-invalid) | La signature ne correspondait pas à la clé du portefeuille | élevée | Une requête a atteint un service externe ; aucun paiement n’a été effectué |
| [`wallet_unlock_throttled`](#wallet-unlock-throttled) | Portefeuille verrouillé après des déverrouillages échoués | élevée | Rien n’a été envoyé, signé ni modifié |

## Intégrité

| Code | Titre | Gravité | Ce qui a peut-être déjà eu lieu |
|---|---|---|---|
| [`evidence_corruption`](#evidence-corruption) | Les enregistrements de la réponse ont échoué à un contrôle de cohérence | élevée | Un enregistrement ou un changement local a eu lieu |
| [`integrity_verification_failure`](#integrity-verification-failure) | Un enregistrement a échoué à son contrôle anti-falsification | critique | Un enregistrement ou un changement local a eu lieu |
| [`unsupported_claim`](#unsupported-claim) | Affirmations non étayées retenues | moyenne | Un enregistrement ou un changement local a eu lieu |
| [`wallet_duplicate_payment`](#wallet-duplicate-payment) | Paiement déjà proposé ou envoyé | moyenne | Un enregistrement ou un changement local a eu lieu |

## Politique

| Code | Titre | Gravité | Ce qui a peut-être déjà eu lieu |
|---|---|---|---|
| [`evm_pocket_custody_unavailable`](#evm-pocket-custody-unavailable) | Poche héritée uniquement pour Solana | faible | Rien n’a été envoyé, signé ni modifié |
| [`permission_denied`](#permission-denied) | Permission refusée | moyenne | Rien n’a été envoyé, signé ni modifié |
| [`wallet_acknowledgement_required`](#wallet-acknowledgement-required) | Confirmation requise | faible | Rien n’a été envoyé, signé ni modifié |
| [`wallet_amount_invalid`](#wallet-amount-invalid) | Montant non exact pour cette pièce | faible | Rien n’a été envoyé, signé ni modifié |
| [`wallet_approval_rejected`](#wallet-approval-rejected) | Approbation non acceptée | moyenne | Rien n’a été envoyé, signé ni modifié |
| [`wallet_confirmation_required`](#wallet-confirmation-required) | Confirmation tapée requise | faible | Rien n’a été envoyé, signé ni modifié |
| [`wallet_credential_mismatch`](#wallet-credential-mismatch) | Les saisies ne correspondaient pas | faible | Rien n’a été envoyé, signé ni modifié |
| [`wallet_dependency_unavailable`](#wallet-dependency-unavailable) | Bibliothèques EVM non installées | moyenne | Rien n’a été envoyé, signé ni modifié |
| [`wallet_device_auth_denied`](#wallet-device-auth-denied) | Déverrouillage par l’appareil refusé | faible | Rien n’a été envoyé, signé ni modifié |
| [`wallet_disabled`](#wallet-disabled) | Le portefeuille est désactivé | faible | Rien n’a été envoyé, signé ni modifié |
| [`wallet_insufficient_funds`](#wallet-insufficient-funds) | Le solde ne couvre pas le montant et les frais | faible | Une requête a atteint un service externe ; aucun paiement n’a été effectué |
| [`wallet_limit_exceeded`](#wallet-limit-exceeded) | Limite de dépense dépassée | moyenne | Rien n’a été envoyé, signé ni modifié |
| [`wallet_not_found`](#wallet-not-found) | Portefeuille, proposition ou destination non reconnus | faible | Rien n’a été envoyé, signé ni modifié |
| [`wallet_password_invalid`](#wallet-password-invalid) | Mot de passe du portefeuille non valide | faible | Rien n’a été envoyé, signé ni modifié |
| [`wallet_paykit_unavailable`](#wallet-paykit-unavailable) | Extra pay pour Solana non installé | moyenne | Rien n’a été envoyé, signé ni modifié |
| [`wallet_payment_channel_off`](#wallet-payment-channel-off) | Canal de paiement désactivé | faible | Rien n’a été envoyé, signé ni modifié |
| [`wallet_pin_invalid`](#wallet-pin-invalid) | Code PIN non valide | faible | Rien n’a été envoyé, signé ni modifié |
| [`wallet_quote_expired`](#wallet-quote-expired) | Aperçu expiré ou remplacé | faible | Rien n’a été envoyé, signé ni modifié |
| [`wallet_recipient_refused`](#wallet-recipient-refused) | Le destinataire ne peut pas recevoir ceci en sécurité | moyenne | Rien n’a été envoyé, signé ni modifié |
| [`wallet_request_ambiguous`](#wallet-request-ambiguous) | Plus d’un compte ou réseau correspond | faible | Rien n’a été envoyé, signé ni modifié |
| [`wallet_send_needs_vool_wallet`](#wallet-send-needs-vool-wallet) | L’envoi nécessite un VOOL Wallet | faible | Rien n’a été envoyé, signé ni modifié |
| [`wallet_setup_state_invalid`](#wallet-setup-state-invalid) | L’étape de configuration ne s’applique pas maintenant | faible | Rien n’a été envoyé, signé ni modifié |
| [`wallet_signing_unavailable`](#wallet-signing-unavailable) | Le portefeuille ne peut pas signer d’ici | moyenne | Rien n’a été envoyé, signé ni modifié |
| [`wallet_storage_class_refused`](#wallet-storage-class-refused) | Stockage des clés uniquement en mémoire | moyenne | Rien n’a été envoyé, signé ni modifié |
| [`wallet_x402_cap_exceeded`](#wallet-x402-cap-exceeded) | Ressource payante au-dessus du plafond automatique | moyenne | Rien n’a été envoyé, signé ni modifié |
| [`wallet_x402_sdk_unavailable`](#wallet-x402-sdk-unavailable) | Extra x402 non installé | moyenne | Rien n’a été envoyé, signé ni modifié |
| [`x402_scheme_unavailable`](#x402-scheme-unavailable) | Schéma de paiement non pris en charge | faible | Rien n’a été envoyé, signé ni modifié |

## Disponibilité

| Code | Titre | Gravité | Ce qui a peut-être déjà eu lieu |
|---|---|---|---|
| [`provider_exhausted`](#provider-exhausted) | Limite du fournisseur atteinte | moyenne | Une requête a atteint un service externe ; aucun paiement n’a été effectué |
| [`provider_unavailable`](#provider-unavailable) | Fournisseur injoignable | moyenne | Rien n’a été envoyé, signé ni modifié |
| [`timeout`](#timeout) | Délai dépassé | moyenne | Résultat inconnu — vérifiez l’état avant de réessayer |
| [`tool_unavailable`](#tool-unavailable) | Outil indisponible | faible | Rien n’a été envoyé, signé ni modifié |
| [`wallet_broadcast_failed`](#wallet-broadcast-failed) | Le paiement n’a pas pu être diffusé | moyenne | Résultat inconnu — vérifiez l’état avant de réessayer |
| [`wallet_device_auth_unavailable`](#wallet-device-auth-unavailable) | Déverrouillage par l’appareil indisponible | moyenne | Rien n’a été envoyé, signé ni modifié |
| [`wallet_quote_unavailable`](#wallet-quote-unavailable) | Solde ou frais inconnus | moyenne | Une requête a atteint un service externe ; aucun paiement n’a été effectué |
| [`wallet_security_key_needed`](#wallet-security-key-needed) | Clé de sécurité requise | faible | Rien n’a été envoyé, signé ni modifié |
| [`wallet_simulation_failed`](#wallet-simulation-failed) | Le paiement a échoué à la simulation | moyenne | Une requête a atteint un service externe ; aucun paiement n’a été effectué |

## Annulation

| Code | Titre | Gravité | Ce qui a peut-être déjà eu lieu |
|---|---|---|---|
| [`cancelled`](#cancelled) | Annulé | informative | Résultat inconnu — vérifiez l’état avant de réessayer |

## Interne

| Code | Titre | Gravité | Ce qui a peut-être déjà eu lieu |
|---|---|---|---|
| [`unknown`](#unknown) | Erreur inattendue | moyenne | Résultat inconnu — vérifiez l’état avant de réessayer |

## Référence par code

Tous les codes, par ordre alphabétique. Le titre de la section est l’ancre stable du code.

<a name="cancelled"></a>

### cancelled

**Annulé**

- **Ce qui s’est passé :** Ceci a été annulé, donc cela n'a pas été terminé.
- **Ce qui a peut-être déjà eu lieu :** Résultat inconnu — vérifiez l’état avant de réessayer
- **Ce qu’il faut faire ensuite :** Rien à faire ; recommencez si vous voulez que ce soit fait.
- **Nouvel essai :** Ne renvoyez pas la même requête sans changement.
- **Gravité :** informative · **Catégorie :** annulation

<a name="confinement-refusal"></a>

### confinement_refusal

**Modification hors du périmètre autorisé**

- **Ce qui s’est passé :** Une modification de fichier hors du périmètre autorisé a été bloquée. Rien hors du périmètre n'a été touché.
- **Ce qui a peut-être déjà eu lieu :** Rien n’a été envoyé, signé ni modifié
- **Ce qu’il faut faire ensuite :** Si cette modification est voulue, demandez-la explicitement pour qu'elle soit dans le périmètre autorisé.
- **Nouvel essai :** Ne renvoyez pas la même requête sans changement.
- **Gravité :** élevée · **Catégorie :** sécurité · **Pertinent pour la sécurité :** oui

<a name="credential-failure"></a>

### credential_failure

**L’identifiant stocké n’a pas pu être lu**

- **Ce qui s’est passé :** Un identifiant stocké n'a pas pu être lu. Saisissez-le à nouveau dans les réglages pour continuer.
- **Ce qui a peut-être déjà eu lieu :** Rien n’a été envoyé, signé ni modifié
- **Ce qu’il faut faire ensuite :** L'entrée du coffre n'a pas pu être déchiffrée ; réenregistrez l'identifiant ou vérifiez la clé du coffre.
- **Nouvel essai :** Ne renvoyez pas la même requête sans changement.
- **Gravité :** élevée · **Catégorie :** sécurité · **Pertinent pour la sécurité :** oui

<a name="evidence-corruption"></a>

### evidence_corruption

**Les enregistrements de la réponse ont échoué à un contrôle de cohérence**

- **Ce qui s’est passé :** Les enregistrements à l'appui de cette réponse ont échoué à un contrôle de cohérence, traitez donc la réponse comme non vérifiée.
- **Ce qui a peut-être déjà eu lieu :** Un enregistrement ou un changement local a eu lieu
- **Ce qu’il faut faire ensuite :** Inspectez le registre d'exécution de ce tour face au flux d'événements du runtime.
- **Nouvel essai :** Ne renvoyez pas la même requête sans changement.
- **Gravité :** élevée · **Catégorie :** intégrité · **Pertinent pour la sécurité :** oui

<a name="evm-pocket-custody-unavailable"></a>

### evm_pocket_custody_unavailable

**Poche héritée uniquement pour Solana**

- **Ce qui s’est passé :** Une poche héritée, le portefeuille fondé sur une phrase de récupération, ne détient qu'une clé Solana, elle ne peut donc pas être créée ni restaurée sur un réseau EVM. Rien n'a été créé. Pour un réseau EVM, créez un Portefeuille VOOL sur ce réseau dans Réglages, Crypto, ajoutez le compte en lecture seule ou connectez votre propre portefeuille EVM.
- **Ce qui a peut-être déjà eu lieu :** Rien n’a été envoyé, signé ni modifié
- **Ce qu’il faut faire ensuite :** La poche héritée scelle une seule graine Solana issue de sa phrase de récupération. Sur un réseau EVM, utilisez un portefeuille Crypto Pilot (« Create wallet » sur ce réseau, dans Réglages, Crypto, Réseaux et portefeuilles), un compte en lecture seule ou un portefeuille EVM externe (EIP-1193) qui garde sa propre clé.
- **Nouvel essai :** Ne renvoyez pas la même requête sans changement.
- **Gravité :** faible · **Catégorie :** politique

<a name="integrity-verification-failure"></a>

### integrity_verification_failure

**Un enregistrement a échoué à son contrôle anti-falsification**

- **Ce qui s’est passé :** Un enregistrement a échoué à son contrôle anti-falsification. Rien n'a été accepté en silence.
- **Ce qui a peut-être déjà eu lieu :** Un enregistrement ou un changement local a eu lieu
- **Ce qu’il faut faire ensuite :** Traitez la chaîne de reçus concernée comme compromise ; investiguez avant de lui faire confiance.
- **Nouvel essai :** Ne renvoyez pas la même requête sans changement.
- **Gravité :** critique · **Catégorie :** intégrité · **Pertinent pour la sécurité :** oui

<a name="permission-denied"></a>

### permission_denied

**Permission refusée**

- **Ce qui s’est passé :** Cette action n'était pas permise, donc rien n'a été changé.
- **Ce qui a peut-être déjà eu lieu :** Rien n’a été envoyé, signé ni modifié
- **Ce qu’il faut faire ensuite :** Ouvrez les Réglages, choisissez Confidentialité et permissions, et réglez « Ce que VOOL peut faire seul » pour changer ce qui régit cette action.
- **Nouvel essai :** Ne renvoyez pas la même requête sans changement.
- **Gravité :** moyenne · **Catégorie :** politique · **Pertinent pour la sécurité :** oui

<a name="provider-exhausted"></a>

### provider_exhausted

**Limite du fournisseur atteinte**

- **Ce qui s’est passé :** La limite d'utilisation du fournisseur de modèles est atteinte. Vous pourrez réessayer plus tard.
- **Ce qui a peut-être déjà eu lieu :** Une requête a atteint un service externe ; aucun paiement n’a été effectué
- **Ce qu’il faut faire ensuite :** Vérifiez quota, limites de débit et facturation du fournisseur configuré.
- **Nouvel essai :** Réessayez plus tard : ce qui l’a empêché se résout avec le temps.
- **Gravité :** moyenne · **Catégorie :** disponibilité

<a name="provider-unavailable"></a>

### provider_unavailable

**Fournisseur injoignable**

- **Ce qui s’est passé :** Le fournisseur de modèles est injoignable. Vous pouvez réessayer dans un instant.
- **Ce qui a peut-être déjà eu lieu :** Rien n’a été envoyé, signé ni modifié
- **Ce qu’il faut faire ensuite :** Vérifiez l'état du fournisseur, la joignabilité réseau et les points de terminaison configurés.
- **Nouvel essai :** Réessayez plus tard : ce qui l’a empêché se résout avec le temps.
- **Gravité :** moyenne · **Catégorie :** disponibilité

<a name="timeout"></a>

### timeout

**Délai dépassé**

- **Ce qui s’est passé :** Cela a pris trop de temps et a été arrêté. Réessayer peut réussir.
- **Ce qui a peut-être déjà eu lieu :** Résultat inconnu — vérifiez l’état avant de réessayer
- **Ce qu’il faut faire ensuite :** Si les délais se répètent, vérifiez la latence réseau et la réactivité du fournisseur.
- **Nouvel essai :** Réessayer maintenant peut réussir.
- **Gravité :** moyenne · **Catégorie :** disponibilité

<a name="tool-unavailable"></a>

### tool_unavailable

**Outil indisponible**

- **Ce qui s’est passé :** Cet outil n'est pas disponible pour l'instant, donc cette partie n'a pas été faite. Il faut l'activer avant de réessayer.
- **Ce qui a peut-être déjà eu lieu :** Rien n’a été envoyé, signé ni modifié
- **Ce qu’il faut faire ensuite :** Ouvrez les Réglages, choisissez Confidentialité et permissions, et réglez « Ce que VOOL peut faire seul », ou choisissez une autre approche.
- **Nouvel essai :** Réessayez après avoir fait la modification indiquée par cette entrée.
- **Gravité :** faible · **Catégorie :** disponibilité

<a name="unknown"></a>

### unknown

**Erreur inattendue**

- **Ce qui s’est passé :** Quelque chose s'est mal passé sur cette machine en traitant cela. Vous pouvez réessayer.
- **Ce qui a peut-être déjà eu lieu :** Résultat inconnu — vérifiez l’état avant de réessayer
- **Ce qu’il faut faire ensuite :** Voyez la chaîne de causes expurgées du défaut dans les diagnostics ; une cause inconnue n'est jamais réessayée à l'aveugle.
- **Nouvel essai :** Ne renvoyez pas la même requête sans changement.
- **Gravité :** moyenne · **Catégorie :** interne

<a name="unsupported-claim"></a>

### unsupported_claim

**Affirmations non étayées retenues**

- **Ce qui s’est passé :** Une ou plusieurs affirmations n'étaient pas étayées par les sources récupérées et ont été retenues.
- **Ce qui a peut-être déjà eu lieu :** Un enregistrement ou un changement local a eu lieu
- **Ce qu’il faut faire ensuite :** Rien à faire — c'est la porte de fondement qui travaille. Si fréquent, vérifiez la qualité de la recherche.
- **Nouvel essai :** Ne renvoyez pas la même requête sans changement.
- **Gravité :** moyenne · **Catégorie :** intégrité

<a name="wallet-acknowledgement-required"></a>

### wallet_acknowledgement_required

**Confirmation requise**

- **Ce qui s’est passé :** Cette étape demande d'abord votre confirmation, donc rien n'a été signé ni envoyé. Lisez ce que le portefeuille dit que cette action fait, confirmez-le exactement, puis réessayez.
- **Ce qui a peut-être déjà eu lieu :** Rien n’a été envoyé, signé ni modifié
- **Ce qu’il faut faire ensuite :** Quand une approbation accorderait des droits de dépense permanents, changerait la propriété ou lancerait une mise à niveau, ou que le portefeuille trouve une incohérence ou ne peut pas lire la demande, il vous demande d'abord de taper son texte de confirmation exact ; rien de générique ne passe à sa place.
- **Nouvel essai :** Réessayez après avoir fait la modification indiquée par cette entrée.
- **Gravité :** faible · **Catégorie :** politique

<a name="wallet-amount-invalid"></a>

### wallet_amount_invalid

**Montant non exact pour cette pièce**

- **Ce qui s’est passé :** Ce montant n'est pas un montant exact que cette pièce peut contenir, donc rien n'a été proposé. Vérifiez le montant, puis réessayez.
- **Ce qui a peut-être déjà eu lieu :** Rien n’a été envoyé, signé ni modifié
- **Ce qu’il faut faire ensuite :** Envoyez un montant décimal simple, pas plus précis que la pièce ne le permet et dans la limite par transfert du pilote.
- **Nouvel essai :** Réessayez après avoir fait la modification indiquée par cette entrée.
- **Gravité :** faible · **Catégorie :** politique

<a name="wallet-approval-rejected"></a>

### wallet_approval_rejected

**Approbation non acceptée**

- **Ce qui s’est passé :** L'approbation de ce paiement n'a pas été acceptée, donc rien n'a été signé ni envoyé.
- **Ce qui a peut-être déjà eu lieu :** Rien n’a été envoyé, signé ni modifié
- **Ce qu’il faut faire ensuite :** Approuvez les paramètres exacts avec le code PIN ou la biométrie du propriétaire ; les approbations ne passent pas d'un paramétrage à l'autre.
- **Nouvel essai :** Ne renvoyez pas la même requête sans changement.
- **Gravité :** moyenne · **Catégorie :** politique · **Pertinent pour la sécurité :** oui

<a name="wallet-backup-unavailable"></a>

### wallet_backup_unavailable

**Sauvegarde déjà montrée une fois**

- **Ce qui s’est passé :** La clé de secours de ce portefeuille a déjà été montrée une fois, elle ne peut plus l'être. Si vous ne l'avez pas notée, annulez la configuration et n'envoyez pas de fonds à cette adresse.
- **Ce qui a peut-être déjà eu lieu :** Rien n’a été envoyé, signé ni modifié
- **Ce qu’il faut faire ensuite :** Une sauvegarde du Pilote Crypto est révélée exactement une fois pendant la configuration ; il n'y a ni seconde révélation ni export.
- **Nouvel essai :** Ne renvoyez pas la même requête sans changement.
- **Gravité :** élevée · **Catégorie :** sécurité · **Pertinent pour la sécurité :** oui

<a name="wallet-broadcast-failed"></a>

### wallet_broadcast_failed

**Le paiement n’a pas pu être diffusé**

- **Ce qui s’est passé :** Le paiement n'a pas pu être diffusé. Vérifiez son statut avant de réessayer.
- **Ce qui a peut-être déjà eu lieu :** Résultat inconnu — vérifiez l’état avant de réessayer
- **Ce qu’il faut faire ensuite :** Vérifiez le point d'accès RPC du réseau et le reçu de la proposition ; la dépense n'a pas été enregistrée comme envoyée.
- **Nouvel essai :** Réessayez plus tard : ce qui l’a empêché se résout avec le temps.
- **Gravité :** moyenne · **Catégorie :** disponibilité

<a name="wallet-caller-refused"></a>

### wallet_caller_refused

**Non lancé depuis la fenêtre propre à VOOL**

- **Ce qui s’est passé :** Cette action de portefeuille ne peut être lancée que depuis la fenêtre de VOOL sur cet ordinateur, donc rien n'a été changé, signé ni envoyé. Ouvrez VOOL et réessayez là-bas.
- **Ce qui a peut-être déjà eu lieu :** Rien n’a été envoyé, signé ni modifié
- **Ce qu’il faut faire ensuite :** Pilotez les portes de portefeuille de confiance depuis la page servie ou la fenêtre native ; une page d'une autre origine, un cadre ou un plugin est refusé.
- **Nouvel essai :** Réessayez après avoir fait la modification indiquée par cette entrée.
- **Gravité :** élevée · **Catégorie :** sécurité · **Pertinent pour la sécurité :** oui

<a name="wallet-card-data-refused"></a>

### wallet_card_data_refused

**Données de carte refusées**

- **Ce qui s’est passé :** Les numéros de carte et codes de sécurité ne sont jamais stockés ici ; seul un jeton de fournisseur est accepté. Rien n'a été enregistré.
- **Ce qui a peut-être déjà eu lieu :** Rien n’a été envoyé, signé ni modifié
- **Ce qu’il faut faire ensuite :** Tokenisez la carte chez le fournisseur et enregistrez la référence du jeton.
- **Nouvel essai :** Ne renvoyez pas la même requête sans changement.
- **Gravité :** élevée · **Catégorie :** sécurité · **Pertinent pour la sécurité :** oui

<a name="wallet-chain-identity-mismatch"></a>

### wallet_chain_identity_mismatch

**Identité du point de terminaison de la chaîne non prouvée**

- **Ce qui s’est passé :** Le point de terminaison de la chaîne n'a pas prouvé l'identité du réseau qu'il prétend être, donc rien n'a été signé ni envoyé.
- **Ce qui a peut-être déjà eu lieu :** Une requête a atteint un service externe ; aucun paiement n’a été effectué
- **Ce qu’il faut faire ensuite :** Vérifiez le point RPC du réseau ; un chain id ou un genesis menteur est refusé avant tout paiement.
- **Nouvel essai :** Ne renvoyez pas la même requête sans changement.
- **Gravité :** élevée · **Catégorie :** sécurité · **Pertinent pour la sécurité :** oui

<a name="wallet-confirmation-required"></a>

### wallet_confirmation_required

**Confirmation tapée requise**

- **Ce qui s’est passé :** Cette étape exige d'abord la confirmation tapée, donc rien n'a été changé. Confirmez, puis réessayez.
- **Ce qui a peut-être déjà eu lieu :** Rien n’a été envoyé, signé ni modifié
- **Ce qu’il faut faire ensuite :** Lisez l'avertissement et tapez la phrase de confirmation exacte pour continuer.
- **Nouvel essai :** Réessayez après avoir fait la modification indiquée par cette entrée.
- **Gravité :** faible · **Catégorie :** politique

<a name="wallet-credential-mismatch"></a>

### wallet_credential_mismatch

**Les saisies ne correspondaient pas**

- **Ce qui s’est passé :** Les deux saisies ne correspondaient pas, donc rien n'a été créé. Entrez deux fois le même code PIN ou mot de passe, puis réessayez.
- **Ce qui a peut-être déjà eu lieu :** Rien n’a été envoyé, signé ni modifié
- **Ce qu’il faut faire ensuite :** Un identifiant du Pilote Crypto est confirmé avant qu'existe le moindre matériel de clé.
- **Nouvel essai :** Réessayez après avoir fait la modification indiquée par cette entrée.
- **Gravité :** faible · **Catégorie :** politique

<a name="wallet-dependency-unavailable"></a>

### wallet_dependency_unavailable

**Bibliothèques EVM non installées**

- **Ce qui s’est passé :** Cette version ne peut ni signer EVM ni encoder l'ABI : les bibliothèques Ethereum ne sont pas installées sur cette machine. Rien n'a été signé ni envoyé. Solana, la garde et les plafonds de dépense ne sont pas affectés ; une fois les bibliothèques installées, réessayez.
- **Ce qui a peut-être déjà eu lieu :** Rien n’a été envoyé, signé ni modifié
- **Ce qu’il faut faire ensuite :** Installez les extras EVM (eth-abi, eth-utils, eth-account) pour activer les voies EVM ; toutes les autres fonctions du portefeuille marchent sans eux.
- **Nouvel essai :** Réessayez après avoir fait la modification indiquée par cette entrée.
- **Gravité :** moyenne · **Catégorie :** politique

<a name="wallet-device-auth-denied"></a>

### wallet_device_auth_denied

**Déverrouillage par l’appareil refusé**

- **Ce qui s’est passé :** Touch ID ou le mot de passe de votre Mac a été refusé ou n'a pas ouvert ce portefeuille, donc rien n'a été créé, modifié, déverrouillé, révélé, signé ni envoyé. Réessayez.
- **Ce qui a peut-être déjà eu lieu :** Rien n’a été envoyé, signé ni modifié
- **Ce qu’il faut faire ensuite :** Réessayez et confirmez la demande de Touch ID ou du mot de passe du Mac affichée par VOOL. Un portefeuille qui approuve avec Touch ID n'a ni code PIN ni mot de passe propre à utiliser à la place. Si la demande échoue encore, restaurez le portefeuille à partir de sa phrase de récupération (un Crypto Pilot, à partir de la sauvegarde montrée à la configuration) et donnez-lui un code PIN ou un mot de passe ; un export de la clé privée demande toujours Touch ID ou le mot de passe du Mac.
- **Nouvel essai :** Réessayez après avoir fait la modification indiquée par cette entrée.
- **Gravité :** faible · **Catégorie :** politique

<a name="wallet-device-auth-unavailable"></a>

### wallet_device_auth_unavailable

**Déverrouillage par l’appareil indisponible**

- **Ce qui s’est passé :** Touch ID ou le mot de passe de votre Mac n'est pas disponible ici pour ce portefeuille, donc rien n'a été créé, modifié, déverrouillé, révélé, signé ni envoyé. Réessayez dès qu'il est disponible.
- **Ce qui a peut-être déjà eu lieu :** Rien n’a été envoyé, signé ni modifié
- **Ce qu’il faut faire ensuite :** Un nouveau portefeuille peut approuver avec son propre code PIN ou mot de passe à la place. Un portefeuille qui approuve avec Touch ID n'a ni code PIN ni mot de passe propre : si Touch ID et le mot de passe du Mac restent indisponibles, restaurez-le à partir de sa phrase de récupération (un Crypto Pilot, à partir de la sauvegarde montrée à la configuration) et donnez-lui un code PIN ou un mot de passe. Un export de la clé privée ne s'ouvre qu'avec Touch ID ou le mot de passe du Mac : restaurez donc le portefeuille sur un Mac où ils fonctionnent et exportez-le là.
- **Nouvel essai :** Réessayez après avoir fait la modification indiquée par cette entrée.
- **Gravité :** moyenne · **Catégorie :** disponibilité

<a name="wallet-disabled"></a>

### wallet_disabled

**Le portefeuille est désactivé**

- **Ce qui s’est passé :** Le portefeuille est désactivé, donc aucun paiement n'a été proposé ni envoyé. Activez « Activer la Crypto » dans Réglages, Crypto, puis réessayez.
- **Ce qui a peut-être déjà eu lieu :** Rien n’a été envoyé, signé ni modifié
- **Ce qu’il faut faire ensuite :** Activez « Activer la Crypto » dans Réglages, Crypto seulement si vous voulez des paiements sur cette installation. Quand la variable VOOL_WALLET_ENABLED est définie, elle active ou désactive le portefeuille quoi qu'indique l'interrupteur.
- **Nouvel essai :** Réessayez après avoir fait la modification indiquée par cette entrée.
- **Gravité :** faible · **Catégorie :** politique

<a name="wallet-duplicate-payment"></a>

### wallet_duplicate_payment

**Paiement déjà proposé ou envoyé**

- **Ce qui s’est passé :** Ce paiement avait déjà été proposé ou envoyé, il n'a donc pas été renvoyé.
- **Ce qui a peut-être déjà eu lieu :** Un enregistrement ou un changement local a eu lieu
- **Ce qu’il faut faire ensuite :** Vérifiez le reçu de la proposition existante ; utilisez une nouvelle clé d'idempotence pour un paiement réellement nouveau.
- **Nouvel essai :** Ne renvoyez pas la même requête sans changement.
- **Gravité :** moyenne · **Catégorie :** intégrité · **Pertinent pour la sécurité :** oui

<a name="wallet-environment-inactive"></a>

### wallet_environment_inactive

**Mauvais environnement réseau**

- **Ce qui s’est passé :** Ce réseau appartient à l'autre environnement réseau (Mainnet ou Réseaux de test), donc rien n'a été créé, proposé ni signé. Changez dans Réglages, Crypto, Options développeur, puis réessayez.
- **Ce qui a peut-être déjà eu lieu :** Rien n’a été envoyé, signé ni modifié
- **Ce qu’il faut faire ensuite :** Choisissez l'environnement réseau délibérément dans les réglages Crypto ; comptes, soldes et reçus ne voyagent jamais entre environnements.
- **Nouvel essai :** Réessayez après avoir fait la modification indiquée par cette entrée.
- **Gravité :** moyenne · **Catégorie :** sécurité · **Pertinent pour la sécurité :** oui

<a name="wallet-export-pin-not-accepted"></a>

### wallet_export_pin_not_accepted

**L’export ne se déverrouille que par l’authentification de l’appareil**

- **Ce qui s’est passé :** L'export ne se déverrouille jamais avec un code PIN, un mot de passe ou une phrase secrète, seulement avec l'authentification de l'appareil (Touch ID ou le mot de passe de votre Mac), donc rien n'a été révélé. Réessayez sans le code PIN ni le mot de passe.
- **Ce qui a peut-être déjà eu lieu :** Rien n’a été envoyé, signé ni modifié
- **Ce qu’il faut faire ensuite :** L'export ne se déverrouille que par l'authentification de l'appareil, à dessein ; un code PIN, un mot de passe ou une phrase secrète dans la requête est refusé avant même d'être vérifié.
- **Nouvel essai :** Réessayez après avoir fait la modification indiquée par cette entrée.
- **Gravité :** élevée · **Catégorie :** sécurité · **Pertinent pour la sécurité :** oui

<a name="wallet-export-refused"></a>

### wallet_export_refused

**Ancienne façon d’exporter retirée**

- **Ce qui s’est passé :** Cette façon d'exporter une clé privée est retirée, donc rien n'a été révélé. Un portefeuille qui permet l'export a un bouton « Export private key » dans Réglages, Crypto, qui ne s'ouvre qu'avec Touch ID ou le mot de passe de votre Mac.
- **Ce qui a peut-être déjà eu lieu :** Rien n’a été envoyé, signé ni modifié
- **Ce qu’il faut faire ensuite :** Utilisez « Export private key » dans Réglages, Crypto, Autres comptes : seule la poche héritée s'exporte, et seulement avec Touch ID ou le mot de passe du Mac. Un portefeuille Crypto Pilot ne s'exporte jamais ; il se récupère à partir de la sauvegarde montrée à la configuration.
- **Nouvel essai :** Ne renvoyez pas la même requête sans changement.
- **Gravité :** élevée · **Catégorie :** sécurité · **Pertinent pour la sécurité :** oui

<a name="wallet-export-unavailable"></a>

### wallet_export_unavailable

**Export indisponible pour ce portefeuille**

- **Ce qui s’est passé :** Ce portefeuille ne peut pas être exporté ainsi : il est en lecture seule ou ne détient aucune clé sur cette machine, sa seule sauvegarde a été montrée à la configuration et il ne s'exporte jamais, VOOL n'exporte pas vers l'application de portefeuille choisie, ou c'est une poche héritée d'une version antérieure qui doit être restaurée à partir de sa phrase de récupération avant de s'exporter vers MetaMask. Rien n'a été révélé.
- **Ce qui a peut-être déjà eu lieu :** Rien n’a été envoyé, signé ni modifié
- **Ce qu’il faut faire ensuite :** Seule une poche héritée scellée sur cette machine s'exporte, vers Phantom ou MetaMask, et seulement avec Touch ID ou le mot de passe du Mac. Un portefeuille Crypto Pilot se récupère à partir de la sauvegarde montrée à la configuration. Une poche héritée d'une version antérieure s'exporte vers MetaMask après avoir été restaurée à partir de sa phrase de récupération.
- **Nouvel essai :** Ne renvoyez pas la même requête sans changement.
- **Gravité :** élevée · **Catégorie :** sécurité · **Pertinent pour la sécurité :** oui

<a name="wallet-insufficient-funds"></a>

### wallet_insufficient_funds

**Le solde ne couvre pas le montant et les frais**

- **Ce qui s’est passé :** Le solde ne couvre pas le montant plus le maximum possible de cette frais de réseau, donc rien n'a été préparé. Baissez le montant ou ajoutez des fonds, puis réessayez.
- **Ce qui a peut-être déjà eu lieu :** Une requête a atteint un service externe ; aucun paiement n’a été effectué
- **Ce qu’il faut faire ensuite :** Le dépensable exclut les autres retenues en cours ; un reliquat Solana doit être sans zéros finals et au moins le loyer.
- **Nouvel essai :** Réessayez après avoir fait la modification indiquée par cette entrée.
- **Gravité :** faible · **Catégorie :** politique

<a name="wallet-legacy-surface-retired"></a>

### wallet_legacy_surface_retired

**Chemin d’argent hérité retiré**

- **Ce qui s’est passé :** Ce chemin d'argent est retiré : chaque paiement passe par le flux de proposition et d'approbation du portefeuille. Rien n'a été signé ni envoyé.
- **Ce qui a peut-être déjà eu lieu :** Rien n’a été envoyé, signé ni modifié
- **Ce qu’il faut faire ensuite :** Utilisez les outils du portefeuille (wallet.propose) ou l'API du portefeuille ; le signataire, le payeur et l'exportateur hérités ne servent plus.
- **Nouvel essai :** Ne renvoyez pas la même requête sans changement.
- **Gravité :** élevée · **Catégorie :** sécurité · **Pertinent pour la sécurité :** oui

<a name="wallet-limit-exceeded"></a>

### wallet_limit_exceeded

**Limite de dépense dépassée**

- **Ce qui s’est passé :** Ce paiement dépasse une limite de dépense, il n'a donc pas été envoyé.
- **Ce qui a peut-être déjà eu lieu :** Rien n’a été envoyé, signé ni modifié
- **Ce qu’il faut faire ensuite :** Relisez les limites par transaction, quotidiennes et par destinataire avant de réessayer.
- **Nouvel essai :** Ne renvoyez pas la même requête sans changement.
- **Gravité :** moyenne · **Catégorie :** politique · **Pertinent pour la sécurité :** oui

<a name="wallet-network-disabled"></a>

### wallet_network_disabled

**Réseau non déclaré pour ce portefeuille**

- **Ce qui s’est passé :** Ce réseau ne fait pas partie de ceux que ce portefeuille déclare pour cette action, donc rien n'a été créé ni envoyé.
- **Ce qui a peut-être déjà eu lieu :** Rien n’a été envoyé, signé ni modifié
- **Ce qu’il faut faire ensuite :** Utilisez une des lignes de réseau déclarées ; chaînes non déclarées, alias et noms ressemblants sont refusés par le registre.
- **Nouvel essai :** Ne renvoyez pas la même requête sans changement.
- **Gravité :** élevée · **Catégorie :** sécurité · **Pertinent pour la sécurité :** oui

<a name="wallet-not-found"></a>

### wallet_not_found

**Portefeuille, proposition ou destination non reconnus**

- **Ce qui s’est passé :** Ce portefeuille, cette proposition ou cette destination n'ont pas été reconnus, donc rien n'a été fait. Vérifiez et réessayez.
- **Ce qui a peut-être déjà eu lieu :** Rien n’a été envoyé, signé ni modifié
- **Ce qu’il faut faire ensuite :** Vérifiez l'id du portefeuille, l'id de proposition ou l'adresse destinataire et réessayez.
- **Nouvel essai :** Réessayez après avoir fait la modification indiquée par cette entrée.
- **Gravité :** faible · **Catégorie :** politique

<a name="wallet-outbound-refused"></a>

### wallet_outbound_refused

**Requête hors des points de terminaison approuvés**

- **Ce qui s’est passé :** Cette requête n'avait pas le droit de quitter les points de terminaison approuvés du portefeuille, donc aucune connexion n'a été établie.
- **Ce qui a peut-être déjà eu lieu :** Rien n’a été envoyé, signé ni modifié
- **Ce qu’il faut faire ensuite :** Vérifiez les origines RPC déclarées et la politique de ressources de paiement ; hôtes privés, métadonnées et non approuvés sont refusés.
- **Nouvel essai :** Ne renvoyez pas la même requête sans changement.
- **Gravité :** élevée · **Catégorie :** sécurité · **Pertinent pour la sécurité :** oui

<a name="wallet-password-invalid"></a>

### wallet_password_invalid

**Mot de passe du portefeuille non valide**

- **Ce qui s’est passé :** Le mot de passe ne respectait pas la politique ou n'a pas déverrouillé le portefeuille, donc rien n'a été signé ni modifié. Vérifiez le mot de passe et réessayez.
- **Ce qui a peut-être déjà eu lieu :** Rien n’a été envoyé, signé ni modifié
- **Ce qu’il faut faire ensuite :** Les mots de passe du portefeuille font de 10 à 128 caractères et ne sont pas uniquement des chiffres. Un mauvais mot de passe laisse la clé scellée et compte pour le verrouillage du portefeuille après trop d'essais erronés.
- **Nouvel essai :** Réessayez après avoir fait la modification indiquée par cette entrée.
- **Gravité :** faible · **Catégorie :** politique

<a name="wallet-paykit-unavailable"></a>

### wallet_paykit_unavailable

**Extra pay pour Solana non installé**

- **Ce qui s’est passé :** Cette version ne peut pas payer les offres x402 ou MPP sur Solana : l'extra optionnel pay (Solana pay-kit, Python 3.11 ou plus récent) n'est pas installé sur cette machine. Rien n'a été signé ni envoyé. Toutes les autres fonctions du portefeuille marchent sans lui ; une fois installé, réessayez.
- **Ce qui a peut-être déjà eu lieu :** Rien n’a été envoyé, signé ni modifié
- **Ce qu’il faut faire ensuite :** Installez l'extra optionnel pay de VOOL sous Python 3.11 ou plus récent pour activer les paiements pay-kit ; le portefeuille garde dans tous les cas son propre signataire, ses plafonds de dépense et son approbation.
- **Nouvel essai :** Réessayez après avoir fait la modification indiquée par cette entrée.
- **Gravité :** moyenne · **Catégorie :** politique

<a name="wallet-payment-channel-off"></a>

### wallet_payment_channel_off

**Canal de paiement désactivé**

- **Ce qui s’est passé :** Ce paiement passe par un canal de paiement désactivé, donc rien n'a été signé ni envoyé. Activez le canal dans Réglages, Crypto, Canaux de paiement, puis réessayez.
- **Ce qui a peut-être déjà eu lieu :** Rien n’a été envoyé, signé ni modifié
- **Ce qu’il faut faire ensuite :** Activez un canal de paiement dans les réglages Crypto seulement si vous voulez que des paiements passent par lui ; le plafond et votre approbation s'appliquent sur chaque canal.
- **Nouvel essai :** Réessayez après avoir fait la modification indiquée par cette entrée.
- **Gravité :** faible · **Catégorie :** politique

<a name="wallet-pin-invalid"></a>

### wallet_pin_invalid

**Code PIN non valide**

- **Ce qui s’est passé :** Le code PIN ne respectait pas la politique ou n'a pas déverrouillé le portefeuille, donc rien n'a été signé. Vérifiez le code PIN et réessayez.
- **Ce qui a peut-être déjà eu lieu :** Rien n’a été envoyé, signé ni modifié
- **Ce qu’il faut faire ensuite :** Utilisez un code PIN de 6 à 12 chiffres ; un mauvais PIN laisse la clé scellée intacte.
- **Nouvel essai :** Réessayez après avoir fait la modification indiquée par cette entrée.
- **Gravité :** faible · **Catégorie :** politique

<a name="wallet-quote-expired"></a>

### wallet_quote_expired

**Aperçu expiré ou remplacé**

- **Ce qui s’est passé :** Cet aperçu de transfert a expiré ou a été remplacé, il ne peut donc pas être approuvé et rien n'a été envoyé. Rafraîchissez l'aperçu, puis réessayez.
- **Ce qui a peut-être déjà eu lieu :** Rien n’a été envoyé, signé ni modifié
- **Ce qu’il faut faire ensuite :** Une cotation vit peu et est supplantée par un rafraîchissement ou un changement d'environnement ; l'approbation lie toujours l'actuelle.
- **Nouvel essai :** Réessayez après avoir fait la modification indiquée par cette entrée.
- **Gravité :** faible · **Catégorie :** politique

<a name="wallet-quote-mismatch"></a>

### wallet_quote_mismatch

**L’approbation ne correspond plus à l’aperçu**

- **Ce qui s’est passé :** L'approbation ne correspond plus au transfert qui avait été prévisualisé, donc rien n'a été signé ni envoyé. Repartez d'un aperçu frais.
- **Ce qui a peut-être déjà eu lieu :** Rien n’a été envoyé, signé ni modifié
- **Ce qu’il faut faire ensuite :** Une approbation lie le condensé d'une cotation ; compte, chaîne, destinataire, montant, environnement ou frais changés exigent une nouvelle approbation.
- **Nouvel essai :** Ne renvoyez pas la même requête sans changement.
- **Gravité :** élevée · **Catégorie :** sécurité · **Pertinent pour la sécurité :** oui

<a name="wallet-quote-unavailable"></a>

### wallet_quote_unavailable

**Solde ou frais inconnus**

- **Ce qui s’est passé :** Le réseau n'a pas donné de réponse complète pour le solde ou les frais de ce transfert, donc aucune approbation n'a été préparée et rien n'a été envoyé. Réessayez dans un instant.
- **Ce qui a peut-être déjà eu lieu :** Une requête a atteint un service externe ; aucun paiement n’a été effectué
- **Ce qu’il faut faire ensuite :** Les données inconnues de chaîne ne sont jamais traitées comme zéro ; vérifiez le point final de la ligne et sa preuve d'identité.
- **Nouvel essai :** Réessayez plus tard : ce qui l’a empêché se résout avec le temps.
- **Gravité :** moyenne · **Catégorie :** disponibilité

<a name="wallet-recipient-refused"></a>

### wallet_recipient_refused

**Le destinataire ne peut pas recevoir ceci en sécurité**

- **Ce qui s’est passé :** Ce destinataire ne peut pas recevoir ce transfert en sécurité sur ce réseau, donc rien n'a été préparé. Vérifiez l'adresse, puis réessayez.
- **Ce qui a peut-être déjà eu lieu :** Rien n’a été envoyé, signé ni modifié
- **Ce qu’il faut faire ensuite :** Refusé : un nouveau compte Solana sous le loyer minimum, un compte exécutable ou hors courbe, une mauvaise somme de contrôle EIP-55, l'adresse zéro, les précompilés et les contrats système.
- **Nouvel essai :** Réessayez après avoir fait la modification indiquée par cette entrée.
- **Gravité :** moyenne · **Catégorie :** politique

<a name="wallet-recovery-refused"></a>

### wallet_recovery_refused

**La preuve de récupération ne correspond pas à ce portefeuille**

- **Ce qui s’est passé :** Cette preuve de récupération ne correspond pas à ce portefeuille, donc rien n'a été changé : la clé enregistrée doit être la sauvegarde que VOOL a montrée pour cette adresse exacte, au format où elle a été montrée. Vérifiez et réessayez, ou utilisez les autres options.
- **Ce qui a peut-être déjà eu lieu :** Rien n’a été envoyé, signé ni modifié
- **Ce qu’il faut faire ensuite :** La récupération rescelle un portefeuille existant uniquement depuis une sauvegarde qui décode vers la clé propre de ce portefeuille (dans les deux sens).
- **Nouvel essai :** Réessayez après avoir fait la modification indiquée par cette entrée.
- **Gravité :** élevée · **Catégorie :** sécurité · **Pertinent pour la sécurité :** oui

<a name="wallet-request-ambiguous"></a>

### wallet_request_ambiguous

**Plus d’un compte ou réseau correspond**

- **Ce qui s’est passé :** Plus d'un compte ou réseau correspond à ce transfert, donc rien n'a été proposé. Dites quel compte ou réseau vous visez, puis réessayez.
- **Ce qui a peut-être déjà eu lieu :** Rien n’a été envoyé, signé ni modifié
- **Ce qu’il faut faire ensuite :** Un transfert nomme un compte sur un réseau ; les candidats sont listés dans le contexte du défaut.
- **Nouvel essai :** Réessayez après avoir fait la modification indiquée par cette entrée.
- **Gravité :** faible · **Catégorie :** politique

<a name="wallet-security-key-needed"></a>

### wallet_security_key_needed

**Clé de sécurité requise**

- **Ce qui s’est passé :** Ce portefeuille a aussi besoin de sa clé de sécurité, et la clé n'a pas répondu : elle n'est pas branchée, c'est une autre clé, ou elle n'a pas été touchée à temps. Rien n'a été déverrouillé, modifié, signé ni envoyé. Branchez la clé de sécurité de ce portefeuille, touchez-la quand elle clignote, puis réessayez.
- **Ce qui a peut-être déjà eu lieu :** Rien n’a été envoyé, signé ni modifié
- **Ce qu’il faut faire ensuite :** Branchez la clé de sécurité ajoutée à ce portefeuille et touchez-la quand elle clignote. Si la clé est perdue, récupérez le portefeuille avec sa clé de sauvegarde (notée sur papier, dans le Trousseau ou sur une clé USB) : cela définit un nouveau code PIN et retire la clé perdue du portefeuille.
- **Nouvel essai :** Réessayez après avoir fait la modification indiquée par cette entrée.
- **Gravité :** faible · **Catégorie :** disponibilité

<a name="wallet-send-needs-vool-wallet"></a>

### wallet_send_needs_vool_wallet

**L’envoi nécessite un VOOL Wallet**

- **Ce qui s’est passé :** Ce compte ne peut pas envoyer depuis VOOL sur ce réseau : seul un VOOL Wallet créé sur cet appareil envoie ici. Un compte connecté ou en lecture seule peut toujours recevoir et afficher son solde. Rien n'a été signé ni envoyé. Pour envoyer, choisissez un VOOL Wallet de cet appareil, puis réessayez.
- **Ce qui a peut-être déjà eu lieu :** Rien n’a été envoyé, signé ni modifié
- **Ce qu’il faut faire ensuite :** Envoyez depuis un VOOL Wallet de cet appareil, ou depuis l'application du portefeuille connecté ; sur ce réseau, VOOL ne signe rien pour un compte connecté ou en lecture seule.
- **Nouvel essai :** Réessayez après avoir fait la modification indiquée par cette entrée.
- **Gravité :** faible · **Catégorie :** politique

<a name="wallet-setup-state-invalid"></a>

### wallet_setup_state_invalid

**L’étape de configuration ne s’applique pas maintenant**

- **Ce qui s’est passé :** Cette étape de configuration ne s'applique pas à ce portefeuille en ce moment, donc rien n'a changé. Vérifiez le statut de configuration du portefeuille, puis réessayez.
- **Ce qui a peut-être déjà eu lieu :** Rien n’a été envoyé, signé ni modifié
- **Ce qu’il faut faire ensuite :** La configuration avance : génération, attente de sauvegarde, sauvegarde révélée, prêt ; annuler et reprendre ne révèlent jamais une sauvegarde deux fois.
- **Nouvel essai :** Réessayez après avoir fait la modification indiquée par cette entrée.
- **Gravité :** faible · **Catégorie :** politique

<a name="wallet-signature-invalid"></a>

### wallet_signature_invalid

**La signature ne correspondait pas à la clé du portefeuille**

- **Ce qui s’est passé :** La signature renvoyée pour ce paiement ne correspondait pas à la clé du portefeuille, elle n'a donc pas été diffusée.
- **Ce qui a peut-être déjà eu lieu :** Une requête a atteint un service externe ; aucun paiement n’a été effectué
- **Ce qu’il faut faire ensuite :** Vérifiez le signataire connecté ; une signature divergente est refusée avant la diffusion.
- **Nouvel essai :** Ne renvoyez pas la même requête sans changement.
- **Gravité :** élevée · **Catégorie :** sécurité · **Pertinent pour la sécurité :** oui

<a name="wallet-signing-unavailable"></a>

### wallet_signing_unavailable

**Le portefeuille ne peut pas signer d’ici**

- **Ce qui s’est passé :** Ce portefeuille ne peut pas signer d'ici : il est en lecture seule ou son signataire externe n'est pas connecté. Rien n'a été envoyé ; connectez un signataire, puis réessayez.
- **Ce qui a peut-être déjà eu lieu :** Rien n’a été envoyé, signé ni modifié
- **Ce qu’il faut faire ensuite :** Connectez le portefeuille externe, ou créez un Portefeuille VOOL sur ce réseau dans Réglages, Crypto.
- **Nouvel essai :** Réessayez après avoir fait la modification indiquée par cette entrée.
- **Gravité :** moyenne · **Catégorie :** politique

<a name="wallet-simulation-failed"></a>

### wallet_simulation_failed

**Le paiement a échoué à la simulation**

- **Ce qui s’est passé :** Le paiement n'a pas passé la simulation, il n'a donc pas été envoyé. Vérifiez le solde et le destinataire, puis réessayez.
- **Ce qui a peut-être déjà eu lieu :** Une requête a atteint un service externe ; aucun paiement n’a été effectué
- **Ce qu’il faut faire ensuite :** Vérifiez le solde, la destination et le point d'accès RPC du réseau, puis proposez à nouveau.
- **Nouvel essai :** Réessayez plus tard : ce qui l’a empêché se résout avec le temps.
- **Gravité :** moyenne · **Catégorie :** disponibilité

<a name="wallet-storage-class-refused"></a>

### wallet_storage_class_refused

**Stockage des clés uniquement en mémoire**

- **Ce qui s’est passé :** Cette installation ne garde sa clé d'appareil qu'en mémoire, un portefeuille créé ici ne pourrait donc pas être rouvert après un redémarrage. Rien n'a été créé. Passez le stockage des clés à un mode durable, puis réessayez.
- **Ce qui a peut-être déjà eu lieu :** Rien n’a été envoyé, signé ni modifié
- **Ce qu’il faut faire ensuite :** Les portefeuilles du Pilote Crypto sont refusés tant que VOOL_KEY_STORAGE_MODE est ephemeral ; utilisez un stockage file ou keyring.
- **Nouvel essai :** Réessayez après avoir fait la modification indiquée par cette entrée.
- **Gravité :** moyenne · **Catégorie :** politique

<a name="wallet-unlock-throttled"></a>

### wallet_unlock_throttled

**Portefeuille verrouillé après des déverrouillages échoués**

- **Ce qui s’est passé :** Il y a eu trop de tentatives erronées de code PIN ou de mot de passe, ce portefeuille est donc verrouillé un moment et rien n'a été déverrouillé. Attendez, puis réessayez.
- **Ce qui a peut-être déjà eu lieu :** Rien n’a été envoyé, signé ni modifié
- **Ce qu’il faut faire ensuite :** Les déverrouillages échoués sont comptés durablement par portefeuille et pour toute l'app, à travers les redémarrages ; le verrou se lève de lui-même.
- **Nouvel essai :** Réessayez plus tard : ce qui l’a empêché se résout avec le temps.
- **Gravité :** élevée · **Catégorie :** sécurité · **Pertinent pour la sécurité :** oui

<a name="wallet-x402-cap-exceeded"></a>

### wallet_x402_cap_exceeded

**Ressource payante au-dessus du plafond automatique**

- **Ce qui s’est passé :** Une ressource payante a demandé plus que le plafond de paiement automatique, elle n'a donc pas été payée.
- **Ce qui a peut-être déjà eu lieu :** Rien n’a été envoyé, signé ni modifié
- **Ce qu’il faut faire ensuite :** Augmentez VOOL_WALLET_X402_CAP_MINOR délibérément, ou payez la ressource par une proposition explicite.
- **Nouvel essai :** Ne renvoyez pas la même requête sans changement.
- **Gravité :** moyenne · **Catégorie :** politique · **Pertinent pour la sécurité :** oui

<a name="wallet-x402-sdk-unavailable"></a>

### wallet_x402_sdk_unavailable

**Extra x402 non installé**

- **Ce qui s’est passé :** Cette version ne peut pas payer les offres x402 sur Base via le x402 Foundation SDK : l'extra optionnel x402 n'est pas installé sur cette machine. Rien n'a été signé ni envoyé. Toutes les autres fonctions du portefeuille marchent sans lui ; une fois installé, réessayez.
- **Ce qui a peut-être déjà eu lieu :** Rien n’a été envoyé, signé ni modifié
- **Ce qu’il faut faire ensuite :** Installez l'extra optionnel x402 de VOOL pour payer les offres x402 sur Base depuis un portefeuille Crypto Pilot ; le portefeuille garde dans tous les cas son propre signataire, ses plafonds de dépense et son approbation.
- **Nouvel essai :** Réessayez après avoir fait la modification indiquée par cette entrée.
- **Gravité :** moyenne · **Catégorie :** politique

<a name="x402-scheme-unavailable"></a>

### x402_scheme_unavailable

**Schéma de paiement non pris en charge**

- **Ce qui s’est passé :** Cette offre de paiement exige un schéma que ce portefeuille ne peut pas prouver de bout en bout, donc rien n'a été signé. Essayez une offre dont le schéma, le jeton et le facilitateur sont vérifiés pour ce réseau.
- **Ce qui a peut-être déjà eu lieu :** Rien n’a été envoyé, signé ni modifié
- **Ce qu’il faut faire ensuite :** Utilisez une offre dont le schéma, le jeton et le facilitateur sont vérifiés pour le réseau ; la prise en charge partielle de signature ne s'improvise jamais.
- **Nouvel essai :** Réessayez après avoir fait la modification indiquée par cette entrée.
- **Gravité :** faible · **Catégorie :** politique

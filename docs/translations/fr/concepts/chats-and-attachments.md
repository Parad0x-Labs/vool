---
description: Les discussions, les discussions privées, et les fichiers et longs collages que vous y apportez : limites, ce qui est conservé et ce qui peut être refusé.
translation: machine
source_sha256: 1497140fef69088f8e0210aeb8a7f6abba6ae82d5132841ce397fe60fa1bab99
---

# Discussions et pièces jointes

Une discussion est une conversation avec VOOL. Les discussions sont conservées sur votre ordinateur. Une
discussion peut être liée à un dossier de projet, pour que ses outils travaillent dans ce dossier (voir
[Espaces de travail](workspaces.md)), et vous pouvez y apporter des fichiers en pièces jointes.

## Joindre des fichiers

Utilisez **Joindre** à côté de la zone de message, ou faites-y glisser des fichiers. Chaque fichier
apparaît sous forme de puce et part avec le prochain message que vous envoyez. La ligne sous la zone
indique les limites :

* Jusqu’à 8 fichiers par message, 10 Mo par fichier et 25 Mo par message.
* Les fichiers texte et de code, et les images PNG, JPEG, GIF et WebP. Les PDF, Word (DOCX), les archives
  comme ZIP et la vidéo s’y ajoutent quand cet ordinateur dispose de ce qu’il faut pour les lire. Le
  sélecteur de fichiers ne propose que ce que cet ordinateur peut lire.

Ce que reçoit le modèle est limité aussi : jusqu’à 120 000 caractères d’un fichier et 240 000 pour
l’ensemble des fichiers d’un message. Un fichier plus long est coupé à la fin d’une ligne, et
l’enregistrement indique où. Si le modèle que vous utilisez ne sait pas lire les images, la puce
l’indique, et l’image ne lui est pas envoyée.

Avant qu’une image soit stockée, ses détails cachés sont retirés : les données d’appareil photo et de
localisation (EXIF, XMP, IPTC), et le texte conservé dans les fichiers PNG et WebP.

Un fichier que vous choisissez sert pour ce seul message. Une fois la réponse terminée, le contenu du
fichier est supprimé et un reçu en est conservé.

## Longs collages

Collez 4 000 caractères ou plus et VOOL les conserve comme un document de la discussion plutôt que comme
un message, exactement tels que collés. **Aperçu** en montre le début. Un document reste avec la
discussion jusqu’à ce que vous l’effaciez, ou que vous effaciez la discussion. Les questions suivantes dans
la même discussion peuvent l’utiliser : VOOL transmet au modèle jusqu’à 60 000 caractères des documents
antérieurs de la discussion, les plus récents d’abord, et indique où il a coupé.

## Quand un fichier est refusé

Un fichier refusé n’est jamais écarté en silence. Sa puce reste et indique pourquoi, et le message ne
peut pas être envoyé tant que vous n’avez pas retiré le fichier ou réessayé : *Retirez ou réessayez la
pièce jointe en échec avant d’envoyer.* Raisons fréquentes :

* **Trop volumineux, ou trop de fichiers.** Retirez-en quelques-uns, ou envoyez-les en deux messages.
* **Un type que VOOL ne lit pas, ou pas ce qu’annonce son nom.** Un fichier nommé `.txt` qui est en
  réalité une image ou un programme, ou un PDF pour lequel cet ordinateur n’a pas de lecteur, est refusé
  avec sa raison.
* **Un identifiant à l’intérieur.** Un fichier ou un collage qui semble contenir une clé ou un jeton
  valide, comme un bloc de clé privée ou un jeton d’accès, est refusé et rien n’est enregistré. Retirez le
  secret, puis joignez ou collez de nouveau.

Ces refus ont leurs propres codes courts, affichés avec le message. Ils ne figurent pas dans le
[Livre des erreurs](../ERROR_BOOK.md), qui couvre les autres erreurs de l’app.

## Discussions privées

Dites « discussion privée », « n’enregistrez pas ça » ou « ça reste entre nous », et la discussion cesse
d’enregistrer à partir de ce message : ni les messages, ni un résumé, ni la mémoire. Un bandeau affiche
*Discussion privée : rien n’est enregistré*, et chaque message privé porte un petit fantôme. Les messages
privés ne reviennent pas après un rechargement, et VOOL ne peut pas s’en souvenir plus tard. Dites
« réactiver l’enregistrement », ou appuyez sur **Réactiver l’enregistrement**, pour enregistrer de nouveau
la discussion à partir du message suivant. Une discussion privée reste privée après le redémarrage de VOOL.

## Emporter une discussion

**Exporter** dans l’en-tête de la discussion enregistre toute la discussion en texte ou en Markdown,
chaque message, avec les fichiers joints listés par leur nom et jamais leur contenu. Pour déplacer une
discussion vers un autre ordinateur, ou garder une copie signée, voir
[Sauvegarde et restauration](../help/backup-and-recovery.md).

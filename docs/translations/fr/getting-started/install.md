---
description: Comment installer l’assistant IA VOOL sur macOS (Apple Silicon ou Intel) ou Linux à partir des téléchargements de la version, ou depuis les sources sur macOS, Windows ou Linux, avec la configuration requise et la vérification d’un téléchargement.
translation: machine
source_sha256: a674148a99297acd314681d6a04bcf2e87e1c88bf00c9e3f25cd22a4083f2177
---

# Installation

VOOL 0.7.7-beta propose des téléchargements de l’app pour les Mac Apple Silicon, les Mac Intel et Linux x86_64
sur la [version v0.7.7-beta](https://github.com/Parad0x-Labs/vool/releases/tag/v0.7.7-beta).
Vous pouvez aussi l’installer depuis les sources avec le script d’amorçage sur macOS, Linux ou Windows.
Consultez [État des versions](../trust/release-status.md) pour la signature et les mises à jour.

## Configuration requise

| | Minimum | Confortable |
| --- | ---: | ---: |
| Mémoire vive | 8 Go | 16 Go ou plus |
| Disque | 2 Go pour l’app | 20 Go avec des modèles locaux |
| macOS | 14 Sonoma, Apple Silicon ou Intel ([limites](#mac-intel)) | 14 Sonoma ou plus récent, Apple Silicon |
| Windows | 10 (64 bits) | 11 |
| Linux | Ubuntu 22.04, x86_64 | Ubuntu 24.04 |

Les modèles locaux demandent nettement plus de mémoire vive que l’app elle-même. Les conseils de
dimensionnement se trouvent dans [Exécuter un modèle local](../guides/local-models.md).

## Télécharger l’app

| Votre ordinateur | Téléchargement |
| --- | --- |
| Mac avec Apple Silicon (M1 et ultérieur) | [`VOOL-0.7.7-beta-macos-arm64.dmg`](https://github.com/Parad0x-Labs/vool/releases/download/v0.7.7-beta/VOOL-0.7.7-beta-macos-arm64.dmg) |
| Mac avec une puce Intel | [`VOOL-0.7.7-beta-macos-x86_64.dmg`](https://github.com/Parad0x-Labs/vool/releases/download/v0.7.7-beta/VOOL-0.7.7-beta-macos-x86_64.dmg) |
| Linux x86_64 (Ubuntu 22.04 / 24.04) | [`vool-0.7.7-beta-linux-x86_64.tar.gz`](https://github.com/Parad0x-Labs/vool/releases/download/v0.7.7-beta/vool-0.7.7-beta-linux-x86_64.tar.gz) |

Pour connaître la puce de votre Mac, ouvrez le menu Apple et choisissez **À propos de ce Mac**. « Apple M… »
signifie Apple Silicon ; « Intel » signifie Intel. L’app Apple Silicon ne fonctionne pas sur un Mac Intel,
et le signale à l’ouverture.

Vérifiez le SHA-256 du téléchargement avant de l’ouvrir (voir [Vérifier un téléchargement](#vérifier-un-téléchargement)).

### Première ouverture sur un Mac

L’app bêta n’est pas encore notarisée par Apple, donc macOS vous demande de confirmer la première fois
que vous l’ouvrez. C’est normal et cela n’arrive qu’une fois.

1. Ouvrez le DMG et faites glisser **VOOL** dans **Applications**.
2. Double-cliquez sur VOOL. macOS indique qu’il ne peut pas vérifier que l’app ne contient pas de logiciel
   malveillant. Fermez le message.
3. Ouvrez **Réglages Système → Confidentialité et sécurité**. Faites défiler jusqu’au message concernant VOOL
   et cliquez sur **Ouvrir quand même**. Saisissez votre mot de passe pour confirmer.

Sous macOS 14 Sonoma, vous pouvez à la place cliquer sur VOOL dans Applications en maintenant la touche
Contrôle enfoncée, choisir **Ouvrir**, puis cliquer de nouveau sur **Ouvrir**.

### Linux

Téléchargez l’archive tar et son fichier `.sha256` depuis la version, vérifiez-la, puis lancez
l’installateur fourni. Ni sudo ni Python système ne sont nécessaires.

```bash
sha256sum -c vool-0.7.7-beta-linux-x86_64.tar.gz.sha256
tar -xzf vool-0.7.7-beta-linux-x86_64.tar.gz
bash vool-0.7.7-beta-linux-x86_64/install.sh
~/.local/bin/vool
```

VOOL s’installe dans `~/.local/share/vool`, place la commande `vool` dans `~/.local/bin` et ajoute
une entrée au menu des applications. L’app de bureau ouvre la discussion dans votre navigateur ; fermer le
navigateur n’arrête pas VOOL. Pour supprimer VOOL, exécutez `bash ~/.local/share/vool/current/uninstall.sh`.
Vos discussions et vos réglages restent dans votre profil.

À partir de 0.7.5, avec **Se connecter de lui-même** activé, le téléchargement Linux se met à jour depuis
VOOL. La nouvelle version est installée à côté
de l’ancienne. Si elle ne démarre pas correctement, VOOL revient de lui-même à l’ancienne.

### Après l’installation

Avant qu’un modèle local puisse répondre, ouvrez **Réglages → Modèles et fournisseurs → Modèles locaux** et appuyez une fois sur **Certify** pour ce modèle (si une vérification a déjà été enregistrée, le bouton indique **Re-run certification**). Cela lance une vérification sur votre machine ; sur un ordinateur sans carte graphique, la première peut prendre plusieurs minutes.

À partir de 0.7.5, VOOL se met à jour tout seul : avec « Se connecter de lui-même » activé, il affiche un avis et installe une nouvelle version en un clic. Mise à jour depuis 0.7.4 ou 0.7.3 sur un Mac : activez « Se connecter de lui-même » (Réglages > Confidentialité et permissions) et appuyez une fois sur « Rechercher des mises à jour ». Depuis 0.7.4 ou antérieur sous Linux, ou 0.7.2 et antérieur sur un Mac : téléchargez 0.7.7 et installez-le par-dessus votre version actuelle. Vos discussions et vos réglages
se trouvent dans votre profil, en dehors de l’app. Les fonctions crypto sont désactivées par défaut.

## Installer depuis les sources

Exécutez le script d’amorçage pour votre plateforme :

```bash
curl -fsSLo bootstrap_vool.sh https://raw.githubusercontent.com/Parad0x-Labs/vool/v0.7.7-beta/installer/bootstrap_vool.sh
bash bootstrap_vool.sh --ref v0.7.7-beta
```

```powershell
Invoke-WebRequest https://raw.githubusercontent.com/Parad0x-Labs/vool/main/installer/bootstrap_vool.ps1 -OutFile bootstrap_vool.ps1
powershell -ExecutionPolicy Bypass -File .\bootstrap_vool.ps1
```

Les commandes macOS / Linux fixent à la fois le script d’amorçage et son téléchargement des sources sur `v0.7.7-beta`. Pour choisir une autre version, changez à la fois le tag dans l’URL du script et `--ref`. Le script d’amorçage accepte un nom de branche ou un tag de version, pas un hash de commit.

L’installation depuis les sources nécessite Python 3.11 ou plus récent. S’il n’en trouve aucun, l’installateur
en télécharge un et l’installe. Les téléchargements de l’app incluent leur propre Python.

L’installateur prépare l’environnement Python, vérifie votre matériel, configure le modèle
local et lance les services locaux. Prévoyez du temps et de l’espace disque pour le premier téléchargement
du modèle. L’inférence des modèles locaux fonctionne hors ligne après la configuration ; les outils web et les
modèles cloud ont besoin d’une connexion.

## Mac Intel

VOOL fonctionne sur les Mac Intel avec macOS 14 ou plus récent (par exemple un MacBook Pro 13 pouces de 2020),
avec le téléchargement Intel ou depuis les sources. L’installateur depuis les sources détecte le processeur Intel
et installe les versions Intel de chaque dépendance. Deux limites sont propres à Intel :

* **Pas d’entraînement de modèle local.** PyTorch ne publie plus de version pour macOS sur Intel depuis la 2.3,
  donc l’installateur l’ignore et l’entraînement LoRA se signale comme indisponible. La discussion, la mémoire,
  les outils et les modèles cloud ne sont pas concernés.
* **Les modèles locaux tournent uniquement sur le CPU.** Les moteurs de modèles locaux n’ont pas d’accélération
  GPU pour les puces graphiques Intel, donc les réponses locales sont plus lentes que sur Apple
  Silicon. Sur un Mac avec 8 Go, utilisez un modèle cloud pour la discussion ; avec 16 Go, le modèle local
  par défaut de l’installateur (un modèle Qwen) fonctionne mais il est lent.

Sur les Mac Intel, les modèles locaux tournent sur le processeur et sont lents. Une longue réponse de mémoire peut dépasser le délai, donc utilisez pour l’instant un modèle cloud sur ces Mac. La certification des modèles locaux tient compte des processeurs plus lents.

Les Mac Intel ont leur propre téléchargement, `VOOL-0.7.7-beta-macos-x86_64.dmg`. Le téléchargement Apple Silicon
ne fonctionne pas sur Intel et le signale au lancement.

## Vérifier un téléchargement

Chaque téléchargement de la version est accompagné d’une somme de contrôle SHA-256. Vérifiez le fichier avant
de l’ouvrir :

```bash
shasum -a 256 <downloaded-artifact>    # macOS
sha256sum <downloaded-artifact>         # Linux
```

```powershell
Get-FileHash -Algorithm SHA256 <downloaded-artifact>
```

Comparez le résultat à la somme de contrôle publiée avec cette version. Si elles diffèrent,
supprimez le fichier et téléchargez-le de nouveau.

## Suite

Continuez avec [Premier lancement](first-run.md).

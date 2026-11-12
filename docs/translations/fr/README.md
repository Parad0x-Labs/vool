---
description: VOOL est un assistant IA de bureau qui exécute des modèles d’IA en local sur votre ordinateur ou se connecte à des modèles cloud avec votre propre clé. Gratuit et privé. Téléchargements pour Mac (Apple Silicon et Intel) et Linux ; Windows est expérimental et s’installe depuis les sources.
translation: machine
source_sha256: c31e5a047068e09c01bbd2e2c90cbbed0b70e78926847a5b737f9e0e625ab1dd
---

# VOOL

VOOL est un assistant IA de bureau. Il s’installe comme une application sur votre ordinateur et peut
exécuter des modèles d’IA en local sur cette machine, ou se connecter à un fournisseur d’IA dans le cloud
avec votre propre clé API. Il tient un journal lisible de chaque action qu’il effectue.

Il est gratuit et fonctionne hors ligne avec des modèles locaux. Des téléchargements existent pour les Mac
(Apple Silicon et Intel) et Linux x86_64 ; Windows est expérimental et s’installe depuis les sources.

Cette documentation explique comment installer VOOL et connecter un modèle, ainsi que les principes de la
façon dont il traite vos fichiers, votre mémoire et vos clés.

## Ses résultats

Sur 150 questions de mémoire LoCoMo jamais utilisées auparavant, avec le même modèle pour répondre à la place
de chaque agent et le même correcteur GPT-4o, la mémoire de VOOL a battu deux autres agents open source :

| | VOOL | OpenClaw | Hermes |
| --- | --- | --- | --- |
| Bonnes réponses | **82,7 %** (124 / 150) | 71,3 % (107) | 62,7 % (94) |
| Tokens par question | **9,6k** | 51,8k | 33,5k |
| Coût par question | **0,0033 $** | 0,0100 $ | 0,0046 $ |

C’est 5,4 fois moins de tokens qu’OpenClaw et 3,5 fois moins que Hermes, pour un tiers du coût par réponse
d’OpenClaw. La mémoire livrée avec VOOL 0.7 obtenait 123 sur 150 aux mêmes questions.
Chaque exécution, la configuration et les points faibles sont publiés : [benchmarks de mémoire](https://github.com/Parad0x-Labs/vool/tree/main/docs/benchmarks).

## Par où commencer

| Vous voulez | Lisez |
| --- | --- |
| Faire fonctionner VOOL | [Installation](getting-started/install.md) |
| Comprendre le premier écran | [Premier lancement](getting-started/first-run.md) |
| Le relier à un modèle | [Connecter un modèle](getting-started/connect-a-model.md) |
| Contrôler les dépenses possibles | [Limites de dépenses](guides/spending-limits.md) |
| Savoir ce qui quitte votre machine | [Local et cloud](concepts/local-and-cloud.md) |

## Ce qu’est VOOL

* **Une application de bureau.** Elle s’installe sur votre ordinateur — ni un onglet de navigateur, ni un
  service hébergé.
* **Fonctionne hors ligne.** Avec un modèle local sélectionné, VOOL n’a besoin d’aucune connexion Internet.
* **Local par défaut.** Avec un modèle local sélectionné, les prompts et les fichiers sont traités
  sur votre machine.
* **Indépendant du modèle.** Les moteurs locaux et les fournisseurs cloud sont tous deux sélectionnables. Vous
  fournissez la clé cloud ; elle reste sur votre machine.
* **Vérifiable.** Chaque appel d’outil est enregistré avec ses entrées et ses résultats, pour qu’une exécution
  puisse être relue après coup.

{% hint style="warning" %}
VOOL est en bêta pour les Mac Apple Silicon, les Mac Intel et Linux x86_64. Les apps Mac
ne sont pas encore notarisées par Apple, donc macOS vous demande de confirmer la première fois que vous en
ouvrez une ; la page [Installation](getting-started/install.md#première-ouverture-sur-un-mac) montre comment faire.
Consultez [État des versions](trust/release-status.md) avant d’installer.
{% endhint %}

## Deux modes, sans ambiguïté

VOOL distingue exactement deux états, et l’interface indique toujours lequel est actif :

1. **Local** — le travail reste sur cet ordinateur.
2. **Cloud** — le contexte sélectionné est envoyé directement au fournisseur que vous avez choisi,
   depuis votre machine, avec votre clé.

Il n’y a pas de troisième état, ni de relais exploité par Parad0x entre les deux. Le détail
se trouve dans [Traitement des données](trust/data-handling.md).

## Obtenir de l’aide

* [FAQ](help/faq.md) — les questions les plus fréquentes
* [Dépannage](help/troubleshooting.md) — quand quelque chose ne démarre pas
* [Discord](https://discord.gg/V9NkjP3Fzz) — posez vos questions à l’équipe

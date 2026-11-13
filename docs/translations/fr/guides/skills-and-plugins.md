---
description: Étendre VOOL avec des compétences et des plugins.
translation: machine
source_sha256: 4dd2a317a7b10ee024616f6e0c27bd73087c44334f29e2c5f60f69773f4e54e8
---

# Compétences et plugins

Une **compétence** est un ensemble d’instructions empaqueté pour une tâche récurrente. Un **plugin**
ajoute des outils, un fournisseur de modèles ou un analyseur de modules. Les plugins peuvent aussi contenir des compétences.

## Fournis ou installés de l’extérieur

VOOL est livré avec une **bibliothèque native de compétences** — les compétences qui viennent avec l’app
elle-même — et, dans les versions actuelles, le plugin **VOOL Database**. Vous pouvez aussi installer vos
propres compétences et plugins.

| | Fourni (livré avec VOOL) | Installé de l’extérieur |
| --- | --- | --- |
| Emplacement | Dans l’app | Votre dossier de plugins (géré depuis la fenêtre des plugins de VOOL, pas depuis les Réglages) |
| Mises à jour | Avec l’app | Quand vous mettez à jour le paquet |
| Peut être désactivé | Pas encore de bouton dans les fenêtres de l’app | Oui — depuis la fenêtre des plugins |
| Accorde des permissions | **Non** | **Non** |

Deux règles valent pour les deux sources :

* **Les compétences enseignent ; elles n’accordent jamais rien.** Les outils déclarés d’une compétence
  sont un signal de *restriction* (le budget d’outils du flux de travail), jamais l’octroi de quoi que ce
  soit. Le contrôleur de permissions ne lit pas les compétences.
* **Un seul inventaire.** Le catalogue que montre la console et les paquets que charge le runtime sont la
  même liste. Qu’une compétence apparaisse dans une liste ne prouve pas qu’elle peut se charger — chaque
  entrée porte son état réel (disponible, désactivée, prérequis manquant, contrat invalide) et la raison.

## Trouver des compétences

« Compétences et plugins » liste d’abord les compétences de VOOL lui-même. **Parcourir** propose 75
compétences VOOL prêtes à l’emploi. Chacune est un ensemble d’instructions, fourni avec l’app, donc
« Parcourir » les liste sans recherche en ligne. 22 d’entre elles utilisent des outils VOOL (vos e-mails, notes, fichiers, PDF, portefeuille et
plus encore) lors des tours où elles sont choisies : les outils d’une compétence ne font que restreindre ce que VOOL propose déjà pour
votre demande, un outil désactivé reste désactivé, et chaque appel demande votre accord comme toujours. Sa section
**Source et détails techniques** liste les outils qu’elle utilise. L’onglet **Codex des compétences** liste d’abord les
compétences de l’équipe VOOL. Examinez une compétence avant de l’activer.

La façon dont une compétence de « Parcourir » est ajoutée dépend de qui l’a créée :

* **Compétences créées par VOOL** (les 75 ci-dessus) : appuyez sur **Ajouter cette compétence**. VOOL vérifie sur votre
  ordinateur que le fichier est exactement celui avec lequel il a été livré et le lit à la recherche d’instructions
  risquées, puis vous montre le résultat. Aucune clé n’est nécessaire et rien n’est envoyé nulle part.
  Si le fichier ne correspond pas, VOOL le refuse et le dit.
* **Compétences d’autres créateurs** (d’autres éditeurs, ou un lien GitHub que vous collez) : appuyez sur
  **Télécharger et analyser**. VOOL télécharge la compétence publique à une version fixe et la vérifie sur
  votre ordinateur. Si vous avez activé un plugin d’analyse, il reçoit aussi le contenu de la compétence et
  son rapport s’ajoute à celui de VOOL. Lisez le résultat, puis choisissez d’ajouter ou non la
  compétence : si l’une des vérifications signale un problème, VOOL ne l’ajoute qu’après que vous avez accepté les risques pour
  cette version. Aucune clé n’est nécessaire, sauf si votre plugin d’analyse en demande une.

Vous pouvez aussi créer votre propre compétence dans une discussion sans aucune clé : voir
[Créer votre propre compétence](../../../guides/make-your-own-skill.md).

Vous pouvez aussi demander dans n’importe quelle discussion : *quelles compétences ai-je ?* les liste, et *comment ajouter une
compétence ?* explique les deux façons.

## Activer ou désactiver une compétence

Ouvrez **Compétences et plugins**, puis **Mes modules**, puis **Compétences**. Chaque compétence affiche **Activé** ou
**Désactivé** avec un bouton **Activer** ou **Désactiver**. Une compétence que vous désactivez n’est utilisée dans aucune discussion
jusqu’à ce que vous la réactiviez. Ses fichiers sont conservés.

La carte **Built-in skills** sous **Extensions** n’a pas de bouton à elle : activez ou désactivez ses compétences
une par une sous **Compétences**.

## Voir quelle compétence a façonné une réponse

Quand les instructions d’une compétence ont servi à VOOL pour écrire une réponse, une courte ligne sous la
réponse l’indique, par exemple **Compétence utilisée : Browser**. Elle apparaît juste après la réponse, tant que
la discussion est ouverte ; les réponses chargées plus tard depuis votre historique ne l’affichent pas encore. Les règles de style de réponse
qui accompagnent la plupart des réponses n’y sont pas nommées.

## Compétences pour construire VOOL

La bibliothèque contient aussi les compétences avec lesquelles l’équipe VOOL construit VOOL : revue de code, exécution de tests,
vérifications de publication, réparation du pipeline et autres. Elles ne sont listées ni dans les discussions ni sur la page des compétences,
et elles n’orientent pas les réponses courantes. Les développeurs peuvent les afficher et les utiliser en lançant VOOL avec
`VOOL_DEVELOPER_SKILLS=1`.

## États de disponibilité

Une compétence peut apparaître comme indisponible avec une raison typée : un prérequis manquant (par
exemple, la compétence de base de données a besoin du plugin de base de données activé), une capacité
indisponible sur cette machine, un contrat invalide, ou une désactivation par vous. Les raisons sont
affichées pour chaque entrée, et la désactivation se fait entrée par entrée et persiste après les
redémarrages.

## Identités en double

Si deux paquets déclarent la même identité de plugin (un paquet fourni et une copie installée, par
exemple), le paquet fourni est conservé **de manière déterministe** et la copie écartée est signalée dans
le catalogue — jamais masquée en silence.

## Installer les vôtres

Installez depuis le catalogue de l’app, ou indiquez à VOOL un dossier de plugins à vous. Les paquets
contiennent un manifeste (`plugin.json`) qui déclare leurs outils ; un manifeste dont les déclarations de
permissions ne correspondent pas à sa classe de comportement déclarée est refusé au chargement.

{% hint style="danger" %}
Examinez le paquet entier avant la vérification. Un examen est une preuve portant sur des octets précis,
pas un certificat de sécurité ni un octroi de permissions.
{% endhint %}

## Plugins dans Modules

Ouvrez **Modules → Mes modules → Extensions**. Examinez un dossier local ou un dossier GitHub public à
un commit exact. L’installation capture le paquet complet et le laisse désactivé. Les plugins signés
de la boutique utilisent la chaîne de registre existante et s’installent aussi désactivés. Examinez la fiche
d’accès séparément avant d’activer des outils ou des services. Enregistrer une clé de service ne déclenche aucune requête ;
les tests de connexion et l’actualisation de la liste des modèles sont des actions explicites.

La désactivation retire les outils et arrête les appels en cours. La désinstallation désactive d’abord, supprime les fichiers propres
et la zone temporaire, supprime par défaut les clés enregistrées et conserve l’historique. Les mises à jour et les réinstallations
exigent un nouvel examen et démarrent désactivées. Une mise à jour provenant d’un autre dépôt ou d’un autre éditeur
ne conserve pas les clés enregistrées. UsePod et Eyebrow ne font plus partie de VOOL, et VOOL
ne propose de plugin pour aucun des deux. Une clé UsePod ou Eyebrow enregistrée reste sur cet ordinateur et VOOL
ne l’utilise pas ; Modules l’indique jusqu’à ce que vous fermiez l’avis. Les discussions qui utilisaient un modèle UsePod
n’ont plus de modèle sélectionné après la mise à niveau, et Auto ne passe pas à un modèle cloud dans ces
discussions ; un avis l’indique jusqu’à ce que vous le fermiez.

Voir [Créer un plugin](../../../guides/plugin-authoring.md) et [Vérifications des modules](addon-security.md).

## Approbation complète des paquets

La vérification couvre chaque fichier et chaque répertoire du paquet, y compris les exécutables des
gestionnaires, les bibliothèques intégrées, les fichiers de verrouillage des dépendances, les références,
les ressources et les bits de mode exécutable. Ajouter, supprimer, renommer ou modifier l’un de ces
éléments invalide l’approbation. Les liens symboliques, les liens physiques, les fichiers spéciaux, les
modes privilégiés, les lectures instables et les distributions trop volumineuses sont refusés. Les limites
de capture actuelles sont de 4 096 entrées, 64 Mio par fichier et 128 Mio par paquet. Il n’y a pas de
répertoires de cache ou d’état ignorés dans un paquet.

Les approbations enregistrées auparavant qui ne portaient que sur les métadonnées n’activent pas de
paquets dans cette version. Examinez la distribution complète, vérifiez-la de nouveau explicitement, puis
activez-la. Les mises à jour restent désactivées jusqu’à ce qu’elles soient vérifiées et activées ;
l’approbation n’est jamais migrée en silence. Les modules gérés ont aussi besoin d’un nouvel examen
authentifié par la boutique quand un ancien reçu ne lie pas la distribution complète ; une vérification
ordinaire ne peut pas remplacer ce reçu.

`requires` nomme les dépendances de plugins installées. La vérification fixe leurs empreintes
d’approbation récursives ; chaque dépendance doit déjà être vérifiée et activée. Une dépendance modifiée,
mise à jour, désactivée ou révoquée rend indisponibles les paquets qui en dépendent, y compris via des
outils en cache. L’admission revérifie l’état activé et révoqué de toute la clôture capturée après le
hachage. Revérifier une dépendance n’approuve pas en silence ses nouveaux octets pour le parent :
examinez et revérifiez aussi chaque parent. Chaque `approval_digest` lie l’empreinte de son paquet et les
approbations de ses dépendances, de sorte qu’une dépendance transitive modifiée change cette approbation
même quand les fichiers propres du parent restent identiques. Une approbation de dépendances cyclique ne
peut pas s’activer. La clôture des dépendances est limitée à 64 paquets.

Chaque invocation enregistrée revérifie l’état du cycle de vie et capture des copies privées en lecture
seule du paquet et de la clôture de dépendances fixée. Le contrat de l’outil doit correspondre au
manifeste capturé. Le gestionnaire enregistré se lance depuis cette capture ; modifier la source après la
capture ne peut remplacer ni ce gestionnaire ni ses fichiers fournis. Les observations d’exécution
incluent les empreintes du paquet, de l’approbation récursive et des dépendances. Les paquets enregistrés
exigent un confinement réel par le noyau, même si un appelant local de bas niveau a choisi une dérogation
plus faible. Les hôtes qui ne peuvent pas capturer de façon sûre par descripteur sans suivre les liens
(no-follow), ou qui n’ont pas de confinement par le noyau, refusent les paquets enregistrés. Les délais
d’invocation sont limités à 1–300 secondes ; le groupe de processus propre à l’invocation est arrêté avant
que sa copie d’exécution soit supprimée.

Conservez les sorties persistantes, les caches et les journaux dans `PLUGIN_SCRATCH`, pas à côté du code
exécutable. Les outils en lecture seule ne reçoivent aucun droit d’écriture. Les autres outils peuvent
écrire dans la zone temporaire mais ne peuvent pas modifier leur copie d’exécution.
`VOOL_PLUGIN_DEPENDENCIES` est une correspondance JSON entre les identités de dépendances déclarées et
les répertoires de dépendances capturés. Intégrez les autres bibliothèques tierces et leurs fichiers de
verrouillage dans le paquet ; un fichier de verrouillage seul n’atteste pas un environnement externe.
L’interpréteur d’exécution et les bibliothèques système restent partie intégrante de la plateforme de
confiance, plutôt que d’être présentés comme des octets de paquets tiers examinés. Ce contrôle
d’intégrité ne certifie pas le comportement, n’atteste pas du code arbitraire téléchargé ou chargé depuis
l’extérieur de la distribution approuvée, ne contrôle pas chaque effet interne et ne remplace pas
l’approbation native du portefeuille.

Sous macOS, le code des plugins s’exécute dans le bac à sable de macOS. Les clés n’atteignent un gestionnaire que par son entrée
privée, jamais par son environnement, car tout programme du même utilisateur, code des plugins compris,
peut lire l’environnement de démarrage d’un autre programme sur un Mac. Un serveur de connexion qui a besoin d’une clé dans son
environnement ne s’exécute que dans le runtime Linux isolé facultatif, que vous provisionnez vous-même ;
VOOL ne démarre jamais de VM et ne télécharge jamais d’image. Sans lui, ce plugin reste désactivé et en indique la raison.
Linux utilise bwrap, avec un espace de noms de processus privé et `/proc`.

Sous macOS, un outil qui déclare un accès réseau ne peut joindre qu’un proxy privé que VOOL démarre pour chaque
appel, et le proxy ne se connecte qu’aux hôtes déclarés. Sous Linux et dans le runtime isolé,
les processus des plugins n’ont pas de réseau. Les requêtes natives des fournisseurs et des analyseurs utilisent leur propre
transport HTTPS vers des hôtes déclarés sur toutes les plateformes.

## Écrire une compétence

* Pour créer une compétence en la demandant dans une discussion, voir [Créer votre propre compétence](../../../guides/make-your-own-skill.md).
* Pour écrire, contrôler (lint), installer et tester vous-même un fichier SKILL.md, voir [Construire et tester une compétence](../../../guides/build-and-test-a-skill.md).

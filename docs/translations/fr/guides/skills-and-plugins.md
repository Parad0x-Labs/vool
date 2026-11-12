---
description: Étendre VOOL avec des compétences et des plugins.
translation: machine
source_sha256: a86860b8624e3c97c81e813ec92e3998c687a87d7bc43b7960c489cdcd309196
---

# Compétences et plugins

Une **compétence** est un ensemble d’instructions empaqueté pour une tâche récurrente. Un **plugin**
ajoute de nouveaux outils au runtime.

## Fournis ou installés de l’extérieur

VOOL est livré avec une **bibliothèque native de compétences** — les compétences qui viennent avec l’app
elle-même — et, dans les versions actuelles, le plugin **VOOL Database**. Vous pouvez aussi installer vos
propres compétences et plugins.

| | Fourni (livré avec VOOL) | Installé de l’extérieur |
| --- | --- | --- |
| Emplacement | Dans l’app | Votre dossier de plugins (défini dans les Réglages) |
| Mises à jour | Avec l’app | Quand vous mettez à jour le paquet |
| Peut être désactivé | Oui — individuellement | Oui — individuellement |
| Accorde des permissions | **Non** | **Non** |

Deux règles valent pour les deux sources :

* **Les compétences enseignent ; elles n’accordent jamais rien.** Les outils déclarés d’une compétence
  sont un signal de *restriction* (le budget d’outils du flux de travail), jamais l’octroi de quoi que ce
  soit. Le contrôleur de permissions ne lit pas les compétences.
* **Un seul inventaire.** Le catalogue que montre la console et les paquets que charge le runtime sont la
  même liste. Qu’une compétence apparaisse dans une liste ne prouve pas qu’elle peut se charger — chaque
  entrée porte son état réel (disponible, désactivée, prérequis manquant, contrat invalide) et la raison.

## Trouver des compétences

« Compétences et plugins » liste d’abord les compétences de VOOL lui-même. **Parcourir** propose 53
compétences VOOL prêtes à l’emploi. Elles ne contiennent que du texte : chacune est un ensemble
d’instructions, fourni avec l’app, donc « Parcourir » les liste sans recherche en ligne. Les compétences
VOOL qui utilisent des outils VOOL sont listées dans l’onglet **Codex des compétences**, où les
compétences de l’équipe VOOL viennent en premier. Examinez une compétence avant de l’activer.

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

## Approbation des paquets dans v0.7

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
L’interpréteur de l’hôte et les bibliothèques système restent partie intégrante de la plateforme de
confiance, plutôt que d’être présentés comme des octets de paquets tiers examinés. Ce contrôle
d’intégrité ne certifie pas le comportement, n’atteste pas du code arbitraire téléchargé ou chargé depuis
l’extérieur de la distribution approuvée, ne contrôle pas chaque effet interne et ne remplace pas
l’approbation native du portefeuille.

## Écrire une compétence

Une compétence est un dossier avec un fichier d’instructions et tous les fichiers auxquels il fait
référence :

```text
my-skill/
├── SKILL.md
└── templates/
```

Rédigez-la dans l’app (`skill.create` la prépare sans rien activer), validez-la, puis installez-la pour
l’activer — trois étapes distinctes à dessein, pour que la rédaction ne soit jamais un changement de
comportement implicite. Le catalogue et le format de rédaction sont encore en cours de finalisation pour
leur sortie publique.

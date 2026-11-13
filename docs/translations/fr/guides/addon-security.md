---
translation: machine
source_sha256: 4596ad3e82d6936445beafa8e30a334325d70a8a0f1ee052eda3c43ae5118244
---
# Vérifications des modules

La section Modules sépare la navigation, l’inspection et l’installation. Une analyse décrit des octets précis ;
ce n’est ni une garantie de sécurité, ni une recommandation de l’éditeur, ni une permission d’exécution, ni une approbation du portefeuille.

« Parcourir » liste d’abord les compétences de VOOL lui-même, dont 75 compétences VOOL fournies avec l’app (22 d’entre
elles utilisent des outils VOOL, qui ne font que restreindre ce que VOOL propose déjà), puis les modules examinés du catalogue. « Codex des compétences » liste d’abord les compétences de l’équipe VOOL, avant les
choix de la rédaction et les compétences à la une.

## Compétences créées par VOOL : la vérification locale

Les 75 compétences créées par VOOL sont livrées dans l’app, chacune fixée par SHA-256 dans le répertoire propre à VOOL et
examinée à la sortie de chaque version de VOOL. Pour celles-ci, **Ajouter cette compétence** n’exécute que la vérification locale de VOOL : aucune clé,
aucun quota d’analyse et aucune requête réseau.

1. VOOL lit la compétence et la licence depuis la copie propre à l’app et les refuse si leur SHA-256
   ne correspond pas à la valeur fixée (*This VOOL skill's file doesn't match the copy that came with VOOL, so it was
   not added.*).
2. La vérification locale de VOOL lit la compétence à la recherche d’instructions risquées et affiche le résultat. Un résultat net
   peut être ajouté avec **Ajouter et activer** ; tout ce qu’elle signale exige votre accord distinct et explicite pour
   cette version précise. **Refuser** l’écarte.
3. L’installation suit ensuite le même cycle de vie que les autres compétences : les fichiers exacts sont installés,
   vérifiés et activés. La compétence installée est vérifiée par rapport à son SHA-256 fixé à chaque
   chargement ; un fichier modifié la rend indisponible.

La vérification locale est une lecture statique d’octets fixes, pas une garantie de sécurité ni de qualité des réponses.
Les compétences d’autres créateurs ne prennent jamais ce chemin.

## Compétences publiques

Choisissez une compétence GitHub publique à une révision fixe et approuvez le téléchargement de son contenu. VOOL la vérifie
sur cet appareil avec `core/local_scan.py`. Sans plugin d’analyse activé, la page explique
que les vérifications sont restées sur cet appareil. Aucune clé d’analyseur n’est nécessaire pour ce chemin.

Un plugin d’analyse activé reçoit aussi le contenu de la compétence publique quand une analyse est demandée. Cela
peut consommer le quota de son service. Les discussions locales, les fichiers privés de l’espace de travail et les autres identifiants ne sont
pas soumis. Enregistrer une clé et ouvrir Modules ne déclenchent ni test de connexion ni demande d’analyse. Il n’y a
aucune nouvelle tentative automatique des requêtes.

Lisez les constats et acceptez l’installation séparément. La validation d’un rapport exige une couverture complète
et des constats valides. Un rapport réussi n’a aucun constat ni aucune violation de politique. Une
acceptation des risques proposée séparément lie la version examinée ; elle ne modifie jamais le rapport et n’accorde jamais davantage d’accès.
Les examens expirés, les octets préparés modifiés, les liens et les dossiers cibles en conflit sont refusés.
Les reçus lient la distribution installée complète. Des fichiers modifiés retirent la disponibilité à l’exécution.
Les consignes d’une compétence sont insérées en entier ou explicitement omises quand elles ne tiennent pas dans le budget de prompt.

## Analyseurs facultatifs

Un analyseur distant est facultatif. Sans plugin d’analyse activé, la vérification propre à VOOL sur cet appareil
décide si une compétence peut être installée ; les versions précédentes exigeaient plutôt une analyse distante.

Pour en ajouter un, installez un plugin d’analyse de son créateur via la section Extensions, enregistrez votre propre
clé, examinez les accès et activez-le. Sa déclaration nomme un chemin pour un test de connexion explicite
et un chemin pour les analyses demandées, et le transport natif commun des plugins effectue ces requêtes.
VOOL n’a ni client d’analyse intégré ni réglage central de clé d’analyseur, et ne propose aucun plugin d’analyse
à lui. Une clé Eyebrow enregistrée par une ancienne version de VOOL reste sur cet ordinateur et n’est pas utilisée. Les
identifiants `vool-plugin-eyebrow` et `vool-plugin-usepod` sont réservés ; un autre paquet qui revendique
l’un d’eux est refusé avant l’examen.

La vérification propre à VOOL sur cet appareil s’exécute toujours. Le rapport d’un plugin d’analyse s’y ajoute :
si l’un ou l’autre trouve un problème, l’installation est bloquée, et le rapport d’un plugin doit nommer le contenu exact
qui a été vérifié. Un plugin d’analyse ne peut jamais approuver une installation à lui seul.

Un analyseur activé dont la clé manque ou est rejetée échoue clairement. VOOL ne remplace jamais en silence
cette analyse distante demandée par un résultat local réussi. Les dépassements de délai, les erreurs du service, les redirections,
les réponses trop volumineuses et les rapports incomplets laissent l’installation non acceptée. Une requête qui a dépassé le délai
peut tout de même avoir consommé du quota du service.

## Paquets exécutables et examen de la boutique

Les plugins passent par un examen complet du paquet, depuis un dossier local ou un paquet GitHub public fixé, distinct de
l’importateur de compétences purement textuelles. L’installation n’exécute aucun code du paquet et le laisse désactivé. Les plugins de la boutique
exigent aussi le reçu signé existant et l’empreinte de la distribution complète. L’activation exige une
fiche d’accès distincte couvrant les outils, les effets, les hôtes exacts, les emplacements d’identifiants et les serveurs fournis.

Le code exécutable ne reçoit que ses propres clés déclarées lors de l’invocation. Il ne peut pas lire les emplacements d’un autre
plugin, les clés de modèles payants du propriétaire, ni exercer d’autorité sur la signature, le plafond, l’approbation ou les reçus du portefeuille.
Sa sortie reste non fiable. Des octets modifiés retirent le consentement ; la désactivation arrête les appels. Un confinement
non pris en charge refuse l’exécution. Consultez [Créer un plugin](../../../guides/plugin-authoring.md) pour les limites d’exécution.

Si VOOL ne peut pas lire ses enregistrements de plugins, les paquets dont il n’a aucun enregistrement cessent de proposer des compétences, à part
celles livrées avec VOOL, et le fichier endommagé est conservé à côté du nouveau. S’il ne peut pas lire
vos choix d’activation et de désactivation, chaque plugin compte comme désactivé jusqu’à ce que vous en réactiviez un.

## API locale et transport

`GET /api/addons` lit l’inventaire. Les modifications via `POST /api/addons` exigent un accès local du propriétaire,
du JSON, des requêtes de même origine depuis le navigateur et des champs propres à l’action. Les points d’accès d’activation et de cycle de vie des plugins
(`POST /api/plugins/enable`, `POST /api/plugins/lifecycle`) appliquent la même règle de JSON et de
même origine. Les téléchargements de sources publiques utilisent
des révisions exactes et des blobs vérifiés de taille limitée. Les services natifs exigent des hôtes HTTPS publics déclarés,
valident TLS, fixent les adresses publiques résolues, refusent les redirections, limitent les réponses et s’arrêtent quand
le plugin est désactivé.

Des services HTTPS locaux simulés peuvent vérifier le transport et la gestion des erreurs sans appels payants. Ils n’établissent
ni la fiabilité réelle du service, ni la facturation, ni la compétence du modèle, ni l’installation dans d’autres
applications. Celles-ci exigent une vérification distincte.

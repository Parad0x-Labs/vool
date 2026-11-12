---
translation: machine
source_sha256: 671f147e5d9649b6c2b7c2d391ea085120e783a8ace29f92ac8c3d5ecb4407eb
---
# Découvrir des compétences et les vérifications Eyebrow

La page « Compétences et plugins » a quatre onglets : **Parcourir**, **Codex des compétences**, **Mes modules** et **Sécurité**. « Parcourir » liste d’abord les compétences de VOOL lui-même, dont 53 compétences VOOL purement textuelles fournies avec l’app, puis les modules examinés du catalogue, et peut consulter un dépôt GitHub public à une révision fixée. « Codex des compétences » liste d’abord les compétences de l’équipe VOOL, avant les choix de la rédaction et les compétences à la une. La recherche ignore la casse et les séparateurs courants ; les commandes de catégorie et de tri restreignent la liste. La source, la licence, la révision et les prérequis sont visibles avant le téléchargement.

## Première utilisation

1. Enregistrez une clé API Eyebrow dans l’onglet « Sécurité », aussi accessible sous Réglages → Clés API. Le stockage des identifiants existant détient la clé. L’enregistrement ne teste pas la connexion et n’analyse rien ; « Tester la connexion » contacte Eyebrow explicitement.
2. Dans « Parcourir », choisissez une compétence et approuvez le téléchargement de son contenu public et l’envoi de ce contenu à Eyebrow. Cela consomme le quota d’analyses du service selon votre formule. Aucune nouvelle tentative automatique n’a lieu.
3. Lisez le rapport renvoyé. Une réponse HTTP réussie ne suffit pas à faire un résultat net. Ce premier catalogue n’autorise l’installation qu’avec un rapport `pass` complet, sans constat et sans violation de politique. « Refuser » supprime le téléchargement préparé sans l’installer.
4. Acceptez l’installation séparément. VOOL conserve la licence, installe par son cycle de vie de plugins existant, vérifie le paquet exact qui a été examiné et active la compétence. Les permissions existantes régissent toujours chaque action qu’elle pourrait suggérer.

Une analyse est l’évaluation d’octets précis par un prestataire, pas une garantie de sécurité, de compatibilité ou de qualité des réponses. L’inclusion dans le catalogue n’est pas une certification de l’éditeur.

## Frontières

Le contenu provient uniquement de dépôts GitHub publics à une révision fixe de 40 caractères, avec des fichiers de compétence et de licence fixés par SHA-256. Il n’y a dans cette version ni dépôt privé, ni extraction d’archives, ni script d’installation, ni importateur de plugins exécutables ou MCP. Seul le SKILL.md autonome est envoyé à Eyebrow ; les discussions locales, les fichiers de l’espace de travail, les identifiants et le contenu des licences ne sont pas soumis à l’analyse.

Les téléchargements restent inertes jusqu’à l’examen et l’acceptation. Des reçus authentifiés localement lient le rapport au contenu fixé. L’installation refuse les examens expirés (24 heures), les fichiers préparés modifiés et les répertoires cibles déjà existants. Le runtime vérifie l’état du cycle de vie et les octets examinés avant d’ajouter une compétence gérée au contexte du modèle. Des fichiers ajoutés, des liens symboliques ou un contenu modifié la rendent indisponible. Les consignes sont insérées en entier ou explicitement enregistrées comme omises faute de budget de prompt suffisant ; les instructions analysées ne sont pas tronquées en silence.

Ces vérifications n’isolent pas un code malveillant qui s’exécute déjà sous l’utilisateur du système d’exploitation. Elles ne s’étendent pas aux plugins installés manuellement auparavant. Un reçu d’analyse n’accorde aucune nouvelle permission d’exécution ni aucune autorité sur le portefeuille.

## API et gestion des échecs

`GET /api/addons` renvoie le catalogue et l’état local sans test de connexion ni analyse. `POST /api/addons` prend en charge `save_key`, `remove_key`, `test_key`, `scan`, `install` et `reject`. Les modifications exigent des requêtes en boucle locale (loopback), en JSON et de même origine depuis le navigateur ; seuls les champs propres à l’opération sont acceptés. Les URL de source et les chemins fournis par l’appelant sont refusés.

Le client utilise la requête de source de compétence intégrée d’Eyebrow v1 à `/v1/scan`, avec une authentification bearer, une origine HTTPS fixe, sans redirections, avec une taille de réponse limitée et sans nouvelle tentative automatique. `/v1/version` sert au test de connexion explicite. Référence de l’API : https://eyebrow.cc/api/reference .

Échecs typés courants :

| Code | Signification / récupération |
| --- | --- |
| `eyebrow_key_missing` / `eyebrow_unauthorized` | Enregistrez ou remplacez la clé. |
| `eyebrow_quota` | Vérifiez le quota du service avant de réessayer. |
| `eyebrow_timeout` / `eyebrow_unreachable` | Aucune nouvelle tentative automatique ; une analyse qui a dépassé le délai peut tout de même consommer du quota. |
| `eyebrow_invalid_report` / `eyebrow_no_coverage` | La preuve est incomplète ; rien n’est activé. |
| `addon_source_changed` / `addon_changed` | Les octets fixés ou examinés diffèrent ; ne les activez pas. |
| `review_untrusted` / `review_expired` | Obtenez un nouvel examen avant l’installation. |
| `addon_already_installed` | Les fichiers existants ont été conservés. Le remplacement automatique n’est pas implémenté. |

## Limites actuelles et vérification

Il n’y a pas de mise à jour automatique, pas d’importateur de catalogues arbitraires, ni de processus de réparation ou de réinstallation sur place pour un paquet existant modifié. La reprise des examens en attente après un redémarrage de l’app n’est pas disponible dans l’interface. Les nouveaux textes de l’interface s’affichent pour l’instant en anglais par défaut ; des traductions complètes ne sont pas revendiquées.

Des tests automatisés exercent le vrai cycle de vie, le chargement des compétences, les gestionnaires de l’API et l’interface de navigateur servie, avec des téléchargements publics et des réponses d’Eyebrow explicitement simulés. Ils prouvent le comportement du runtime et de l’interface, pas la disponibilité réelle d’Eyebrow ni la compétence du modèle. Une vraie analyse avec une clé et l’acceptation de l’app native empaquetée restent des vérifications de publication. Aucune clé ni aucun appel à un service payant n’est nécessaire pour exécuter ces tests.

---
description: Comment VOOL protège vos fichiers et vos clés API — limites de l’espace de travail, stockage des identifiants et ce qu’un plugin peut atteindre.
translation: machine
source_sha256: 4397ca0e7b494efacb90aa546eae10800b9a58558ba7c611c0131f4410c2fe3a
---

# Modèle de sécurité

## Frontières

* **Les limites de dossiers** encadrent l’accès aux fichiers. VOOL lit et modifie les fichiers dans le
  dossier de projet de la discussion. En dehors, VOOL peut lire vos dossiers personnels visibles et ne
  modifier des fichiers que dans Bureau, Téléchargements et Documents ; les dossiers cachés, les données
  des apps et du système et les données de VOOL lui-même ne sont jamais lus. Voir
  [Espaces de travail](../concepts/workspaces.md).
* **Le stockage des identifiants** détient les clés. Les clés ne sont pas écrites dans les fichiers de
  configuration ni dans les journaux.
* **La confirmation des outils** encadre les modifications. En mode Manuel, chaque modification demande
  d’abord ; en mode Auto, les modifications et les commandes courantes peuvent s’exécuter
  automatiquement, tandis que la suppression, l’écrasement, l’installation, les dépenses et la
  modification des réglages demandent toujours. Voir [Outils](../concepts/tools.md).

## Votre réseau local

Par défaut, VOOL n’écoute que sur cet ordinateur. Quand vous le réglez pour écouter sur votre réseau
local, seul cet ordinateur est traité comme vous. Les réglages, les fichiers, les clés, les outils et les
fonctions privées ne répondent qu’à lui.

Un autre appareil du réseau obtient la discussion simple. Il peut aussi lire une page web publique à la
fois. Cette page doit se trouver sur l’Internet public : les adresses sur cet ordinateur ou sur votre
réseau local sont refusées, et chaque redirection est vérifiée de la même façon. Les recherches web, la
recherche documentaire, le navigateur, les cotations, les paiements et vos clés payantes ne fonctionnent
que sur cet ordinateur.

## Pont Discord

Le pont Discord ne répond qu’à son propriétaire. Le premier utilisateur Discord qui lui envoie une
commande devient le propriétaire. Pour choisir vous-même le propriétaire, définissez
`VOOL_DISCORD_OWNER_USER_ID` sur votre identifiant d’utilisateur Discord ; faites-le pour tout canal où
d’autres personnes peuvent écrire. Tous les autres sont informés une fois que ce VOOL est réservé à son
propriétaire, et leurs messages ne sont pas traités. Même les messages Discord du propriétaire ont les
mêmes limites qu’un autre appareil du réseau.

## Clés

VOOL conserve vos clés de fournisseur dans un fichier chiffré dans son « runtime home » sur cet
ordinateur (voir [Configuration](../reference/configuration.md)), lisible uniquement par votre compte
utilisateur. Les clés que VOOL conservait déjà dans le trousseau macOS sur un Mac y restent lisibles.
Une clé est utilisée depuis votre machine pour joindre votre fournisseur. Elle n’est pas envoyée à
Parad0x Labs, et il n’y a aucun point de terminaison exploité par Parad0x sur le chemin de la requête.

{% hint style="info" %}
Le fichier chiffré est protégé par les permissions de fichiers de votre compte utilisateur, pas par un
mot de passe distinct. Laissez le chiffrement du disque activé (FileVault sur un Mac) pour qu’un disque
perdu ou volé n’expose pas vos clés.
{% endhint %}

## Plugins

Un plugin s’exécute avec les accès du runtime. C’est ce qui rend les plugins utiles et ce qui justifie
de les lire avant de les installer. Voir [Compétences et plugins](../guides/skills-and-plugins.md).

## Signaler un problème

Signalez les problèmes de sécurité en privé via
[GitHub Security Advisories](https://github.com/Parad0x-Labs/vool/security/advisories/new),
jamais dans un ticket public. Indiquez la version, la plateforme et les étapes pour reproduire le
problème.

{% hint style="warning" %}
Les apps macOS sont signées ad hoc et ne sont pas notarisées ; l’archive tar Linux n’est pas signée.
Voir [État des versions](release-status.md).
{% endhint %}

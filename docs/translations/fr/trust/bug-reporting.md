---
description: Signaler un bug en toute sécurité — brouillons expurgés, approbation des octets exacts, et ce qui ne quitte jamais votre machine.
translation: machine
source_sha256: 4e64e6313c1f6a8be6784a1f35cedc87b81a5661fdb548c6821109f935db818f
---

# Signalement de bugs et confidentialité

## Le rapporteur de bugs intégré

Utilisez le rapporteur de bugs de l’app (ou la CLI `python -m apps.vool_cli bug-report`). Il n’envoie
jamais rien de lui-même — le processus comporte délibérément trois étapes :

1. **Brouillon** — un rapport est construit localement à partir des journaux et diagnostics récents, et
   expurgé au fur et à mesure. *« Rien n’est encore envoyé — un brouillon local est créé d’abord. »*
2. **Aperçu** — vous voyez les **octets exacts** qui seraient envoyés, avec une empreinte du contenu et
   un résumé de l’expurgation qui liste ce qui a été retiré. Les champs que vous ne voulez pas envoyer
   (journaux, indicateurs, notes de reproduction, pièces jointes) peuvent être retirés ; toute
   modification réinitialise l’approbation.
3. **Approuver** — l’envoi exige votre approbation explicite de ces octets exacts. Si le contenu change,
   l’approbation ne s’applique plus et vous devez approuver de nouveau.

Après votre approbation, **Open on GitHub** ouvre dans votre navigateur une page de rapport préremplie
sur le dépôt public [vool-reports](https://github.com/Parad0x-Labs/vool-reports). Relisez-la là-bas une
dernière fois et appuyez sur Submit. VOOL lui-même n’envoie rien, et aucun jeton GitHub n’est nécessaire.
**Copy report** et **Save a local copy** ne demandent aucune approbation : ils vous remettent les mêmes
octets nettoyés et rien ne quitte votre machine. **My reports** liste vos brouillons et la façon dont
chacun est parti.

## Signaler une mauvaise réponse

Appuyez sur **Signaler** ou 👎 sous n’importe quelle réponse, ou surlignez la partie erronée et appuyez
sur **Signaler**. Le rapport ne cite que cette partie (ou toute la réponse). Votre question est laissée
de côté, sauf si vous cochez la case. Le reçu de la réponse est inclus : modèle, fournisseur, tokens,
durée et étapes exécutées, jamais leur contenu. Une réponse que vous avez demandé à VOOL d’oublier ne
peut pas être signalée.

## Ce qui est ajouté pour aider l’enquête

La configuration de votre ordinateur : version du système d’exploitation, puce, mémoire vive, GPU et
version du serveur de modèles local. Aussi une courte vérification de santé : si le serveur de modèles
local répond, et l’espace disque libre sous forme de plage. Jamais le nom de votre ordinateur, votre nom
d’utilisateur, les numéros de série ou les adresses réseau. Vous pouvez retirer n’importe lequel de ces
éléments dans l’aperçu.

## Ce que l’expurgation retire

De manière déterministe, avant même que vous voyiez le brouillon : les identifiants et les en-têtes
d’authentification, les chemins du dossier personnel et les noms d’utilisateur, le nom de votre
ordinateur, les e-mails, les adresses IP, les PIN et les codes à usage unique, les numéros de carte et de
compte bancaire (IBAN), les numéros de téléphone, les adresses de portefeuille, les données personnelles
enregistrées dans votre profil, les secrets étiquetés et les blocs à forte entropie — le tout complété
par une dernière passe de recherche de secrets.
VOOL ne peut pas reconnaître les noms ni les faits privés tapés dans des phrases ordinaires, donc lisez l’aperçu. Les traces de pile sont reconstruites à partir d’une liste d’autorisation, pas collées telles quelles.
Les preuves d’échec dans les rapports ne contiennent **que des codes typés** — jamais de contexte interne
ni de chaînes de causes.

Chaque valeur retirée est remplacée par un marqueur. Un e-mail, une personne, un dossier personnel, une
adresse IP, un numéro de téléphone, une adresse de portefeuille, le nom de votre ordinateur ou une donnée
de votre profil garde un même marqueur numéroté dans tout le rapport, comme `[EMAIL_1]` et `[EMAIL_2]`
pour deux adresses différentes, pour que le rapport reste lisible.
Chaque clé, mot de passe, PIN, numéro de carte ou de compte bancaire retiré apparaît comme
`[SECRET_REMOVED]`, jamais numéroté. La liste qui relie un marqueur à la vraie valeur n’est conservée que
dans la mémoire vive de VOOL pendant que vous travaillez sur le rapport, pour qu’une modification garde
les mêmes marqueurs. Elle n’est jamais enregistrée ni envoyée, et elle est oubliée quand vous supprimez
le rapport, après une heure sans utilisation, ou quand VOOL redémarre.

## Ce que VOOL ne fait jamais

* Aucun signalement externe automatique, de quelque nature que ce soit.
* Aucune chaîne de télémétrie en attente derrière un réglage.
* Les problèmes de sécurité passent par le canal privé décrit dans la
  [politique de sécurité](https://github.com/Parad0x-Labs/vool/security/policy) du dépôt — jamais par un
  ticket public.

## Résumé de confidentialité

* Avec un modèle local sélectionné, les prompts et les fichiers sont traités sur votre machine.
* Les appels cloud utilisent la clé API que vous avez fournie, stockée localement ; les clés ne voyagent
  jamais dans les rapports.
* Le détail complet des flux de données se trouve dans [Traitement des données](../trust/data-handling.md).

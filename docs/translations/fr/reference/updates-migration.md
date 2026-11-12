---
description: Comment VOOL se met à jour, ce qu’il advient de vos données, et la compatibilité entre versions.
translation: machine
source_sha256: 3b6a950556f4669f116dfa3a911ffb5e07ad8bcfa04415d68cf5a3d1adf0fe65
---

# Mises à jour et migration

## Fonctionnement des mises à jour

À partir de 0.7.5, VOOL se met à jour tout seul : avec « Se connecter de lui-même » activé, il affiche un avis et installe une nouvelle version en un clic. Mise à jour depuis 0.7.4 ou 0.7.3 sur un Mac : activez « Se connecter de lui-même » (Réglages > Confidentialité et permissions) et appuyez une fois sur « Rechercher des mises à jour ». Depuis 0.7.4 ou antérieur sous Linux, ou 0.7.2 et antérieur sur un Mac : téléchargez 0.7.7 et installez-le par-dessus votre version actuelle. Vos discussions et vos réglages se trouvent dans votre profil, en dehors de l’app.

Avec « Se connecter de lui-même » activé, VOOL recherche une nouvelle version au démarrage et une fois
par jour, et affiche un avis sans que vous ayez à appuyer sur « Rechercher des mises à jour ». Quand ce
réglage est désactivé, VOOL ne récupère rien et l’en-tête affiche
**Recherche de mises à jour désactivée**.

Un paquet de mise à jour signé suit un processus vérifié avec retour arrière automatique :

1. **Télécharger** — vérifié par rapport à un manifeste signé.
2. **Installation préparée** — la nouvelle version est installée à côté de l’ancienne.
3. **Migration** — vos données sont migrées vers la nouvelle version du stockage. Si la migration
   échoue, vos données sont restaurées dans la version précédente et l’app redémarre sur celle-ci :
   *« Vos données ont été restaurées dans la version précédente. »*
4. **Contrôle de santé** — la nouvelle version doit prouver qu’elle fonctionne avant que l’ancienne soit
   retirée.
5. **Bascule** — atomique ; la version précédente est conservée comme cible de retour arrière.

Sous Linux, l’installation par archive tar se met à jour de la même façon. La nouvelle version est
installée à côté de celle qui tourne, et `current` bascule vers elle en une seule étape. Si la nouvelle
version n’est pas en bon état, VOOL revient de lui-même à la précédente. Un **Roll back** manuel est
proposé sur le Mac et pour les installations depuis les sources, mais pas encore pour l’installation par
archive tar Linux.

Si une étape échoue, l’app reste telle qu’elle était — le message d’échec nomme l’étape
(`verification_failed`, `insufficient_disk_space`, …) et chaque étape a sa propre phrase de
récupération.

## Compatibilité des données

* Votre profil, vos discussions, vos reçus et vos réglages se trouvent en dehors du paquet de l’app,
  donc remplacer l’app ne remplace jamais vos données.
* Le stockage local refuse de s’ouvrir après un **retour à une version antérieure** : une base de données
  écrite par un VOOL plus récent ne sera pas lue en silence par un plus ancien — il le dit et refuse, car
  deviner une structure de stockage inconnue la corromprait. Réinstallez la version plus récente pour
  lire les nouvelles données.
* Les fichiers installés par les mises à jour et l’état de mise à jour lui-même sont enregistrés dans
  votre profil avec des reçus, donc « quelle version a touché mes données » trouve une réponse après
  coup.

## Canal bêta

La ligne de versions actuelle est une **bêta** (voir [État des versions](../trust/release-status.md)).
Les mises à jour sont signées par la clé d’éditeur épinglée et vérifiées avant leur installation. L’app
macOS elle-même est signée ad hoc et n’est pas notarisée par Apple, donc macOS peut vous demander de
confirmer le premier lancement d’une nouvelle version.

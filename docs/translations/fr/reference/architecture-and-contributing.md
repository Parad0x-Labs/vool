---
description: Comment VOOL est construit — l’architecture en une page, la configuration pour les développeurs et comment contribuer.
translation: machine
source_sha256: 7cb91029e16c6510821e1ca26b509da8cdfb63113111619d3a31e364a2f088d3
---

# Architecture et contribution

## L’architecture en une page

VOOL est un runtime de bureau qui privilégie le local, avec un cœur servi :

```text
┌──────────────────────────────────────────────┐
│ Native window / browser console              │
│  (served pages; the console you use)         │
├──────────────────────────────────────────────┤
│ Served runtime (local HTTP API)              │
│  routing → tool execution → answers          │
├───────────────┬──────────────┬───────────────┤
│ Local models  │ Cloud lanes  │ Wallet (opt)  │
│ (Ollama etc.) │ (your keys)  │ pilot lanes   │
├───────────────┴──────────────┴───────────────┤
│ Records: receipts, ledgers, fault registry   │
│ Local storage (SQLite under your profile)    │
└──────────────────────────────────────────────┘
```

Principes que le code fait respecter :

* **Local par défaut ; le cloud avec votre clé.** Les voies cloud n’existent qu’avec vos identifiants et
  vos plafonds.
* **Une seule autorité par décision.** Les permissions, les dépenses et le trafic réseau sortant ont
  chacun un seul contrat responsable ; les échecs sont classés à la frontière qui en est responsable.
* **Dire ce qui a échoué.** Les résultats inconnus sont typés comme inconnus ; les refus disent ce qui ne
  s’est pas produit ; un modèle fixé n’est jamais remplacé en silence.
* **Tout est enregistré.** Les appels d’outils, les approbations, les paiements et les mises à jour
  écrivent des reçus dans votre profil.

## Configuration pour les développeurs

```bash
git clone https://github.com/Parad0x-Labs/vool.git
cd vool
python3 -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
pytest tests -q            # the suite
```

Les notes propres aux plateformes, l’organisation complète de la chaîne d’outils et les étapes de
validation des versions se trouvent dans la documentation technique du dépôt (`docs/` à la racine du
dépôt ; les rouages internes détaillés ne font pas partie de ce site de documentation public).

## Documentation : une seule source de référence

Cette documentation est générée à partir d’**une seule** source : le dossier `docs/` de ce dépôt
(organisation GitBook, `.gitbook.yaml` à la racine du dépôt). Le site de documentation public est
construit à partir de lui. Pour travailler sur la documentation :

```bash
python3 tools/docs/build_docs.py --src . --out docs-build --base /docs
python3 tools/docs/check_docs_build.py docs-build /docs
python3 -m http.server -d docs-build 8000   # local preview
```

La référence des erreurs (`ERROR_BOOK.md`) et le manifeste de transmission de la localisation sont
**générés à partir des registres propres au runtime** — régénérez-les, ne les modifiez jamais à la main :

```bash
python -m tools.generate_error_book
python -m tools.generate_localization_handoff
```

Un test ciblé fait échouer la construction si les fichiers générés s’écartent de leurs registres.

## Contribuer

* De petits changements relus ; chaque affirmation de la documentation doit correspondre au code tel
  qu’il est livré.
* Préservez les codes et identifiants stables — ils survivent aux versions.
* N’élargissez jamais une autorité en silence : une nouvelle permission, une nouvelle voie de dépense ou
  une nouvelle voie de sortie réseau est un changement de conception, pas un correctif.
* Les limitations de la bêta sont documentées, pas cachées — si vous en trouvez une non documentée, c’est
  un bug de la documentation.

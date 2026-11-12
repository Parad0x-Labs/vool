---
description: Kaip sukurtas VOOL — architektūra viename puslapyje, kūrėjo aplinkos parengimas ir kaip prisidėti.
translation: machine
source_sha256: 7cb91029e16c6510821e1ca26b509da8cdfb63113111619d3a31e364a2f088d3
---

# Architektūra ir prisidėjimas

## Architektūra viename puslapyje

VOOL yra pirmiausia vietoje veikianti darbalaukio vykdymo aplinka su teikiamu branduoliu:

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

Principai, kurių laikosi kodas:

* **Pagal numatytuosius nustatymus vietoje; debesija — su jūsų raktu.** Debesijos maršrutai egzistuoja tik
  su jūsų prisijungimo duomenimis ir jūsų viršutinėmis ribomis.
* **Vienas sprendėjas kiekvienam sprendimui.** Leidimai, išlaidos ir išeinantis tinklo srautas turi po
  vieną juos valdantį kontraktą; klaidos klasifikuojamos toje riboje, kuriai jos priklauso.
* **Įvardyti, kas nepavyko.** Nežinomi rezultatai žymimi kaip nežinomi; atmetimai pasako, kas neįvyko;
  prisegtas modelis niekada tyliai nepakeičiamas.
* **Viskas įrašoma.** Įrankių iškvietimai, patvirtinimai, mokėjimai ir atnaujinimai įrašo kvitus jūsų
  profilyje.

## Kūrėjo aplinkos parengimas

```bash
git clone https://github.com/Parad0x-Labs/vool.git
cd vool
python3 -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
pytest tests -q            # the suite
```

Pastabos apie platformas, visa įrankių grandinės struktūra ir leidimo patikros pateiktos repozitorijos
inžinerinėje dokumentacijoje (`docs/` repozitorijos šakniniame kataloge; gilios vidinės detalės nėra šios
viešos dokumentacijos svetainės dalis).

## Dokumentacija: vienas kanoninis šaltinis

Ši dokumentacija generuojama iš **vieno** šaltinio: šios repozitorijos aplanko `docs/` (GitBook struktūra,
`.gitbook.yaml` repozitorijos šakniniame kataloge). Iš jo kuriama vieša dokumentacijos svetainė. Norėdami
dirbti su dokumentacija:

```bash
python3 tools/docs/build_docs.py --src . --out docs-build --base /docs
python3 tools/docs/check_docs_build.py docs-build /docs
python3 -m http.server -d docs-build 8000   # local preview
```

Klaidų knyga (`ERROR_BOOK.md`) ir lokalizacijos perdavimo manifestas yra
**generuojami iš pačios vykdymo aplinkos registrų** — generuokite juos iš naujo, niekada neredaguokite
rankiniu būdu:

```bash
python -m tools.generate_error_book
python -m tools.generate_localization_handoff
```

Tikslinis testas sustabdo kūrimą, jei sugeneruoti failai nukrypsta nuo savo registrų.

## Prisidėjimas

* Maži, peržiūrėti pakeitimai; kiekvienas dokumentacijos teiginys turi atitikti išleistą kodą.
* Išsaugokite pastovius kodus ir identifikatorius — jie išlieka ilgiau nei versijos.
* Niekada tyliai neplėskite įgaliojimų: naujas leidimas, išlaidų kelias ar išeinančio srauto maršrutas yra
  dizaino pakeitimas, o ne pataisa.
* Beta apribojimai dokumentuojami, o ne slepiami — jei randate nedokumentuotą apribojimą, tai yra
  dokumentacijos klaida.

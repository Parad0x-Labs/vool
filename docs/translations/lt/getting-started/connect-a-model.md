---
description: Kaip prie VOOL prijungti DI modelį: vietinį modelį, veikiantį jūsų kompiuteryje, arba debesijos tiekėją su jūsų pačių API raktu.
translation: machine
source_sha256: 53f84267e6c9d76240039e6119dfb654fc0e2f479f3bd293ad48232d2df0dd01
---

# Modelio prijungimas

VOOL nepateikiamas su modeliu. Numatytai jis parenka vietinį modelį, o jūs galite pasirinkti kitą;
šis pasirinkimas lemia, ar užklausa lieka jūsų kompiuteryje, ar jį palieka.

## Lokaliai

Jei vietinė vykdymo aplinka įdiegta ir veikia, VOOL ją aptinka ir parodo jos modelių sąrašą.
Pasirinkus vieną iš jų, indikatorius rodo Lokaliai. Išsamiau apie parengimą rašoma skyriuje
[Vietinio modelio paleidimas](../guides/local-models.md).

## Debesys

Tiekėjo raktą pridėkite skiltyje **Nustatymai → API raktai**. Raktas saugomas šifruotame faile šiame
kompiuteryje (žr. [Saugumo modelis](../trust/security.md#raktai)) ir naudojamas užklausoms siųsti
**iš jūsų kompiuterio tiesiai tiekėjui**.

Pasirinkus debesijos modelį, indikatorius rodo tiekėjo pavadinimą, ir jis pasikeičia dar prieš
išsiunčiant bet kokią užklausą.

Konkrečiai OpenRouter eiga aprašyta skyriuje [OpenRouter prijungimas](../guides/openrouter.md).

## Perjungimas

Perjungus modelį, perjungiamas ir režimas. Indikatorius yra lemiamas: jei jis rodo Lokaliai,
kita užklausa apdorojama šiame kompiuteryje.

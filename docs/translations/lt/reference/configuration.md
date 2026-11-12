---
description: Kur VOOL saugo savo konfigūraciją ir ką reiškia nustatymai.
translation: machine
source_sha256: 3bbb23b29003bdd8c973c2d88a34faf974f6436405b7c90da4d0307c0962f887
---

# Konfigūracija

## Vietos

VOOL savo nustatymus, pokalbius, atmintį ir įrašus laiko viename aplanke „runtime home“:

| Įdiegimas | Aplankas „runtime home“ |
| --- | --- |
| macOS programa | `~/Library/Application Support/VOOL/runtime` |
| Linux tar archyvas ir įdiegimai iš šaltinio kodo | `~/.vool_runtime` |
| Windows (eksperimentinis, iš šaltinio kodo) | `%LOCALAPPDATA%\VOOL` |

Jei prieš paleisdami VOOL nustatote `VOOL_HOME`, vietoj šio aplanko naudojamas nurodytasis. Paleidimo
žurnaluose nurodomas naudojamas aplankas.

Raktai **nėra** įrašomi į nustatymų failus ar žurnalus. Jie laikomi užšifruoti atskirame prisijungimo
duomenų faile šiame aplanke „runtime home“, kurį gali perskaityti tik jūsų naudotojo paskyra; raktai,
kuriuos VOOL jau laikė macOS Keychain Mac kompiuteryje, ten ir lieka. Žr.
[Saugumo modelis](../trust/security.md#raktai).

## Nustatymai

Nustatymus keiskite programoje, skiltyje **Nustatymai**. Tie, kurie lemia, ką VOOL gali daryti:

* **Modelis** — aktyvus modelis, kuris lemia, ar ėjimas vykdomas LOKALIAI, ar DEBESYSE.
* **Darbo sritis** — projekto aplankas, kuriame dirba pokalbis; apie kitus aplankus, kuriuos VOOL gali
  pasiekti, žr. [Darbo sritys](../concepts/workspaces.md).
* **Veikimo režimas** — pasirenkamas meniu šalia žinutės laukelio, o ne čia. Režimu Rankinis klausiama
  prieš kiekvieną pakeitimą; režimu Automatinis kasdieniai redagavimai ir komandos vykdomi, o dėl trynimo,
  perrašymo, diegimo, išlaidų ir nustatymų keitimo vis tiek klausiama. Žr.
  [Leidimai](../concepts/permissions.md).
* **Prieiga prie žiniatinklio ir kriptovaliutos** — abi lieka išjungtos, kol jų neįjungiate.

VOOL neturi produkto telemetrijos nustatymo, nes nesiunčia jokios produkto telemetrijos.

## Atstatymas iš naujo

Uždarykite VOOL ir perkelkite aplanką „runtime home“ kitur. Darbo srities failai nepaliečiami; VOOL vėl
pasileidžia su nauju profiliu. Jūsų išsaugoti raktai perkeliami kartu su tuo aplanku, todėl vėl juos
pridėkite skiltyje **Nustatymai → API raktai**. macOS Keychain laikomi raktai ten ir lieka, kol jų
nepašalinate programoje Keychain Access.

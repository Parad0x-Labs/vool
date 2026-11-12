---
description: Kalbos, prieinamumas, veiklos istorija, kvitai ir kaip tikrinami modelių atsakymai.
translation: machine
source_sha256: 2e135f9673ae465cee9db53572ab89c93235805dfea713efd73d7f4244b6b1f5
---

# Kalbos, prieinamumas ir įrašai

## Sąsajos kalbos

VOOL sąsaja prieinama keliomis kalbomis. Kalbos pasirinkimas lemia tik pateikimą: sprendimai dėl leidimų,
įgaliojimai išlaidoms ir klaidų kodai yra **nepriklausomi nuo kalbos pačia savo sandara** — išverstas
klaidos pranešimas niekada negali išplėsti ar susiaurinti to, ką nusprendė angliškasis sprendimų šaltinis.

## Prieinamumas

* Spartieji klavišai pagrindiniams veiksmams (žr. [Spartieji klavišai](shortcuts.md)).
* Šviesus ir tamsus dokumentacijos ir programos vaizdas, atitinkantis jūsų sistemos nustatymą, su galimybe
  jį pakeisti rankiniu būdu.
* Klaidos nurodomos tekstu, o ne vien spalva; negrįžtamiems veiksmams visada reikia aiškaus patvirtinimo,
  o ne vieno atsitiktinio spustelėjimo.

## Veikla

Veiklos rodinys yra visa istorija to, ką VOOL padarė: įrankių iškvietimai su jų įvestimis ir rezultatais,
jūsų suteikti ar atmesti patvirtinimai ir mokėjimai su jų kvitais. Ėjimas, kurį sustabdė apsauga, rodo
sustabdymą ir tą apsaugą — atmetimas yra įrašytas įvykis, o ne įrašo nebuvimas.

## Kvitai

Su pinigais ir išlaidomis susiję veiksmai įrašo **kvitus**: kas buvo pasiūlyta, kas patvirtinta, kas
išsiųsta ir kas atsiskaityta. Nežinomi rezultatai įrašomi kaip nežinomi — niekada nepateikiami kaip sėkmė
ar nesėkmė. Būtent todėl nurodymą *„Prieš bandydami dar kartą, patikrinkite jo būseną“* galima įvykdyti:
kvitas parodo, kokios būsenos mokėjimas iš tikrųjų yra.

## Modelių patikros būsena

VOOL skiria tai, ką modelis **pasakė**, nuo to, ką vykdymo aplinka **įrodė**:

* Įrankių rezultatai įrašomi kartu su jų vykdymo įrašais; atsakymo teiginiai apie failus, mokėjimus ar
  duomenis susiejami su tais įrašais, o nepagrįsti teiginiai sulaikomi.
* Kvitai turi patikros būseną; kai maršrutas šioje versijoje yra eksperimentinis arba jo neįmanoma
  patikrinti, būsena tai ir pasako, užuot leidusi manyti, kad yra įrodymas.

Sąvoka aprašyta skyriuje [Kvitai](../concepts/receipts.md), o tai, kaip klasifikuojamos nesėkmės, —
skyriuje [Klaidos](errors.md).

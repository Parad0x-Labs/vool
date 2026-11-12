---
description: Kvitas yra įrašas apie tai, ką vykdymas iš tikrųjų atliko.
translation: machine
source_sha256: caffc8be79cb1017a81f74baa2451e076db2e6660302b57db4b9d3ec92dab8b8
---

# Kvitai

Kvitas yra patvarus vykdymo įrašas: kokie įrankiai buvo iškviesti, su kokiomis įvestimis ir ką
kiekvienas iš jų grąžino.

## Kam jie reikalingi

Agento paties darbo santrauka yra tik teiginys. Kvitas yra įrašas, su kuriuo galite tą teiginį
patikrinti.

## Kas jame yra

| Laukas | Reikšmė |
| --- | --- |
| `tool` | Koks veiksmas atliktas |
| `input` | Kas jam buvo pateikta |
| `result` | Ką jis grąžino |
| `time` | Kada jis atliktas |

Kvitai saugomi VOOL aplanke „runtime home“ šiame kompiuteryje (žr.
[Konfigūracija](../reference/configuration.md)), o ne jūsų projektų aplankuose. Kvitas jūsų kompiuterį
palieka tik klaidos ataskaitoje, kurią peržiūrite ir nusprendžiate išsiųsti (žr.
[Pranešimai apie klaidas ir privatumas](../trust/bug-reporting.md)).

---
description: Kaip naudoti VOOL kaip DI programavimo asistentą su savo kodo baze — darbo srities nustatymas, klausimai apie kodą ir redagavimų peržiūra.
translation: machine
source_sha256: 1d1c4e7c4222eed4dc6caae24dca70729496ead8b88b1fc45a81a61ed548241a
---

# Programavimas su VOOL

## Nukreipkite darbo sritį į repozitoriją

Nukreipkite [darbo sritį](../concepts/workspaces.md) į projekto šakninį katalogą. VOOL skaito tik
reikalingus failus, o ne įkelia visą medį.

## Klauskite kodo terminais

Nurodę failo ar simbolio pavadinimą, suteikiate vykdymo aplinkai atspirties tašką:

> Rask, kur `src/auth/` nustatomas sesijos laukimo laikas, ir paaiškink, kas nutinka, kai
> jis baigiasi.

## Peržiūrėkite prieš pritaikydami

Režimu Rankinis dėl kiekvieno redagavimo pirmiausia klausiama. Režimu Automatinis kasdieniai redagavimai
ir komandos gali būti vykdomi automatiškai ir rodomi veiklos įraše, o dėl trynimų ir perrašymų vis tiek
klausiama. Prieš didelį pakeitimą padarykite commit, kad skirtumus (diff) būtų lengva perskaityti ir
lengva atšaukti.

## Lokaliai ar debesijoje

Programavimas veikia abiem režimais. 7B vietinis programavimo modelis susidoroja su dauguma kasdienių
redagavimų; didesni debesijos modeliai susidoroja su ilgesniu samprotavimu. Modelio indikatorius parodo,
kuris iš jų aktyvus, dar prieš jums išsiunčiant: `LOCAL` vietiniam modeliui, `CLOUD` debesijos modeliui ir
numatytai `AUTO` (`VOOL Auto`, `Local first`), kai renkasi VOOL.

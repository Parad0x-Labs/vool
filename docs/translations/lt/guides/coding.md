---
description: Kaip naudoti VOOL kaip DI programavimo asistentą su savo kodo baze — darbo srities nustatymas, klausimai apie kodą ir redagavimų peržiūra.
translation: machine
source_sha256: 112dfd866a96b0f7fd22f6f57f7f6480d7ac293466fdf0260a74f3bc0b870036
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
redagavimų; didesni debesijos modeliai susidoroja su ilgesniu samprotavimu. Režimo indikatorius parodo,
kuris iš jų aktyvus, dar prieš jums išsiunčiant.

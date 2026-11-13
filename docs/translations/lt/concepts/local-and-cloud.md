---
description: Kokie duomenys palieka jūsų kompiuterį, kai naudojate VOOL, ir kada. Kuo skiriasi DI paleidimas vietoje nuo debesijos modelio naudojimo.
translation: machine
source_sha256: 2c2b9e26e99579c00a032dfeac95fd216fe9229497c965c1bc08aface7a6c071
---

# Vietoje ir debesijoje

VOOL gali atsakyti dviem būdais: šiame kompiuteryje arba per debesijos tiekėją. Pokalbio antraštė
rodo, kuriuo būdu bus vykdoma kita užklausa, — pavyzdžiui, **AUTOMATINIS** su
**Pirmiausia vietoje**, kai VOOL Auto atsako iš šio kompiuterio.

## Lokaliai

Modelis ir jūsų failai lieka šiame kompiuteryje. Modelis veikia vietinėje vykdymo aplinkoje, failai skaitomi
iš disko, ir jokiam tiekėjui nesiunčiama jokia užklausa. Naujausia informacija, pavyzdžiui, orai ar kainos, gaunama iš viešųjų žiniatinklio paslaugų tik tada, kai įjungta „Interneto paieška“ (po naujo įdiegimo ji išjungta), ir niekada režimu „Tik lokaliai“.

## Debesys (tiekėjo pavadinimas)

Pasirinktas kontekstas siunčiamas tiesiai jūsų pasirinktam tiekėjui, iš jūsų kompiuterio,
naudojant jūsų raktą.

„Pasirinktas kontekstas“ reiškia tai, ką programa surinko tai užklausai: jūsų užklausos tekstą ir
failus bei ankstesnius ėjimus, kurių tai užklausai reikia.

{% hint style="danger" %}
Debesijos režimu duomenys siunčiami už jūsų kompiuterio ribų. Būtent tam debesijos režimas ir skirtas. VOOL teiginys
yra apie *maršrutą* — užklausa keliauja iš jūsų kompiuterio jūsų tiekėjui, — o ne apie tai, kad
niekas neperduodama.
{% endhint %}

## Kas yra tarp jų

Nieko, ką valdytų Parad0x Labs. Modelių srautui nėra jokio tarpinio serverio, šliuzo ar retransliatoriaus.
Jūsų raktą naudoja programa jūsų kompiuteryje, kad tiesiogiai susisiektų su tiekėju.

Išsamiau: [Duomenų tvarkymas](../trust/data-handling.md).

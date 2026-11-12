---
description: Kokie duomenys palieka jūsų kompiuterį, kai naudojate VOOL, ir kada. Kuo skiriasi DI paleidimas vietoje nuo debesijos modelio naudojimo.
translation: machine
source_sha256: 3217e13bf6d684853b92d19097627d07e64bdd17745560b1204871cb0b7b0753
---

# Vietoje ir debesijoje

VOOL turi dvi būsenas. Sąsaja visada rodo, kuri iš jų aktyvi.

## Lokaliai

Viskas lieka šiame kompiuteryje. Modelis veikia vietinėje vykdymo aplinkoje, failai skaitomi
iš disko, ir jokiam tiekėjui nesiunčiama jokia užklausa.

## Debesys (tiekėjo pavadinimas)

Pasirinktas kontekstas siunčiamas tiesiai jūsų pasirinktam tiekėjui, iš jūsų kompiuterio,
naudojant jūsų raktą.

„Pasirinktas kontekstas“ reiškia tai, ką programa surinko tai užklausai: jūsų užklausos tekstą ir
failus bei ankstesnius ėjimus, kurių tai užklausai reikia. Jis parodomas prieš išsiunčiant.

{% hint style="danger" %}
Debesijos režimu duomenys siunčiami už jūsų kompiuterio ribų. Būtent tam debesijos režimas ir skirtas. VOOL teiginys
yra apie *maršrutą* — užklausa keliauja iš jūsų kompiuterio jūsų tiekėjui, — o ne apie tai, kad
niekas neperduodama.
{% endhint %}

## Kas yra tarp jų

Nieko, ką valdytų Parad0x Labs. Modelių srautui nėra jokio tarpinio serverio, šliuzo ar retransliatoriaus.
Jūsų raktą naudoja programa jūsų kompiuteryje, kad tiesiogiai susisiektų su tiekėju.

Išsamiau: [Duomenų tvarkymas](../trust/data-handling.md).

---
description: Saugus pranešimas apie klaidas — juodraščiai be jautrių duomenų, tikslių baitų patvirtinimas ir kas niekada nepalieka jūsų kompiuterio.
translation: machine
source_sha256: 4e64e6313c1f6a8be6784a1f35cedc87b81a5661fdb548c6821109f935db818f
---

# Pranešimai apie klaidas ir privatumas

## Įtaisytas pranešimų apie klaidas įrankis

Naudokite programoje esantį pranešimų apie klaidas įrankį (arba komandų eilutės įrankį
`python -m apps.vool_cli bug-report`). Jis niekada nieko nesiunčia pats — procesas sąmoningai turi tris
etapus:

1. **Juodraštis** — ataskaita sukuriama vietoje iš naujausių žurnalų ir diagnostikos duomenų, o jautrūs
   duomenys pašalinami jau ją kuriant. *„Nothing is sent yet — a local draft is created first.“*
2. **Peržiūra** — matote **tikslius baitus**, kurie būtų išsiųsti, su turinio maiša ir pašalintų duomenų
   suvestine, kurioje išvardyta, kas pašalinta. Laukus, kurių nenorite siųsti (žurnalai, vėliavėlės,
   pastabos apie atkartojimą, priedai), galima pašalinti; bet koks redagavimas panaikina patvirtinimą.
3. **Patvirtinimas** — siuntimui reikia jūsų aiškaus būtent tų baitų patvirtinimo. Jei turinys
   pasikeičia, patvirtinimas nebegalioja ir turite patvirtinti iš naujo.

Jums patvirtinus, **Open on GitHub** jūsų naršyklėje atidaro užpildytą ataskaitos puslapį viešoje
[vool-reports](https://github.com/Parad0x-Labs/vool-reports) repozitorijoje. Ten ją dar kartą
perskaitykite ir paspauskite Submit. Pats VOOL nieko nesiunčia, ir GitHub prieigos rakto nereikia.
**Copy report** ir **Save a local copy** visai nereikia patvirtinimo: jie pateikia jums tuos pačius
išvalytus baitus, ir niekas nepalieka jūsų kompiuterio. **My reports** išvardija jūsų juodraščius ir tai,
kaip kiekvienas iš jų buvo perduotas.

## Pranešimas apie neteisingą atsakymą

Paspauskite **Pranešti** arba 👎 po bet kuriuo atsakymu arba pažymėkite neteisingą dalį ir paspauskite
**Pranešti**. Ataskaitoje cituojama tik ta dalis (arba visas atsakymas). Jūsų klausimas neįtraukiamas,
nebent pažymite langelį. Įtraukiamas atsakymo kvitas: modelis, tiekėjas, žetonai, laikas ir kurie žingsniai
buvo vykdomi, bet niekada ne jų turinys. Apie atsakymą, kurį paprašėte VOOL pamiršti, pranešti negalima.

## Kas pridedama tyrimui palengvinti

Jūsų kompiuterio sandara: OS versija, lustas, operatyvioji atmintis, GPU ir vietinio modelių serverio
versija. Taip pat trumpa būklės patikra: ar vietinis modelių serveris atsako ir kiek laisvos vietos diske
yra (intervalu). Niekada ne jūsų kompiuterio pavadinimas, jūsų naudotojo vardas, serijos numeriai ar tinklo
adresai. Peržiūroje galite pašalinti bet kurią iš šių dalių.

## Kas pašalinama

Deterministiškai, dar prieš jums pamatant juodraštį: prisijungimo duomenys ir autentifikavimo antraštės,
namų aplankų keliai ir naudotojų vardai, jūsų kompiuterio pavadinimas, el. pašto adresai, IP adresai,
PIN kodai ir vienkartiniai kodai, kortelių ir banko sąskaitų (IBAN) numeriai, telefono numeriai, piniginių
adresai, jūsų profilyje išsaugoti asmens duomenys, pažymėti slapti duomenys ir didelės entropijos duomenų
blokai — o pabaigoje visa tai dar patikrinama galutiniu slaptų duomenų skenavimu.
VOOL negali atpažinti vardų ar privačių faktų, įrašytų įprastais sakiniais, todėl perskaitykite peržiūrą. Steko pėdsakai atkuriami pagal leidžiamųjų elementų sąrašą, o ne įklijuojami neapdoroti.
Gedimų įrodymuose ataskaitose yra **tik tipizuoti kodai** — niekada ne vidinis kontekstas ar priežasčių
grandinės.

Kiekviena pašalinta reikšmė pakeičiama žyma. El. pašto adresas, asmuo, namų aplankas, IP adresas,
telefono numeris, piniginės adresas, jūsų kompiuterio pavadinimas ar jūsų profilio duomuo visoje
ataskaitoje išlaiko vieną numeruotą žymą, pavyzdžiui, `[EMAIL_1]` ir `[EMAIL_2]` dviem skirtingiems
adresams, kad ataskaitą vis tiek būtų lengva skaityti.
Kiekvienas pašalintas raktas, slaptažodis, PIN kodas, kortelės ar banko sąskaitos numeris rodomas kaip
`[SECRET_REMOVED]` ir niekada nenumeruojamas. Sąrašas, siejantis žymą su tikrąja reikšme, laikomas tik VOOL
operatyviojoje atmintyje, kol dirbate su ataskaita, todėl redaguojant išlieka tos pačios žymos. Jis niekada
neišsaugomas ir nesiunčiamas, o pamirštamas, kai ištrinate ataskaitą, po valandos nenaudojimo arba kai
VOOL paleidžiamas iš naujo.

## Ko VOOL niekada nedaro

* Jokio automatinio išorinio pranešinėjimo.
* Jokios telemetrijos grandinės, laukiančios už nustatymo.
* Saugumo problemos teikiamos per privatų kanalą, aprašytą repozitorijos
  [saugumo politikoje](https://github.com/Parad0x-Labs/vool/security/policy) — niekada ne per viešą
  pranešimą.

## Privatumo santrauka

* Pasirinkus vietinį modelį, užklausos ir failai apdorojami jūsų kompiuteryje.
* Debesijos iškvietimams naudojamas jūsų pateiktas API raktas, saugomas vietoje; raktai niekada nepatenka
  į ataskaitas.
* Visa informacija apie duomenų srautus pateikta skyriuje [Duomenų tvarkymas](../trust/data-handling.md).

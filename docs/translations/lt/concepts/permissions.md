---
description: Penki režimai, ką kiekvienas iš jų leidžia VOOL daryti neklausiant ir ką apima kiekvienas patvirtinimo mygtukas.
translation: machine
source_sha256: 1f33c8d63bcdeddbd1ac44d3b2d948b354137954ee6accb6ee18eeec8bdda1c6
---

# Leidimai

VOOL nusprendžia, kas gali būti vykdoma, dar prieš ką nors vykdydamas. Modelis gali paprašyti veiksmo;
ar jis įvyks, lemia jūsų pasirinktas režimas ir jūsų atsakymai. Skaitymas vertinamas kitaip nei keitimas,
o slapti duomenys, mokėjimai ir saugos nustatymai apsaugoti bet kuriuo režimu.

## Režimai

Režimą pasirinkite meniu šalia žinutės laukelio. Naujas pokalbis pradedamas režimu **Rankinis**.

| Režimas | Vykdoma neklausiant | Pirmiausia klausiama | Niekada |
| --- | --- | --- | --- |
| Rankinis | Failų skaitymas ir sąrašų rodymas, saugios komandos ir vieša tik skaitymo paieška žiniatinklyje | Dėl kiekvieno pakeitimo: failų redagavimo, komandų, tinklo veiksmų, nustatymų, Git, išskleidimo ir žinučių | Išsaugotų slaptų duomenų skaitymas |
| Redagavimų peržiūra | Tas pats kaip režimu Rankinis | Tas pats kaip režimu Rankinis. Kiekviena redagavimų grupė prieš pritaikant parodoma kaip peržiūrimas pakeitimas | Išsaugotų slaptų duomenų skaitymas |
| Planas | Failų skaitymas ir sąrašų rodymas bei saugios komandos | Dėl nieko: jis tik skaito ir siūlo planą | Kiekvienas pakeitimas, kiekvienas tinklo veiksmas |
| Automatinis | Failų skaitymas, kūrimas ir redagavimas, komandos, tinklo ir naršyklės veiksmai bei modelių tiekėjai | Dėl trynimo, esamų failų perrašymo, priklausomybių diegimo, tiekėjų nustatymų keitimo, Git commit, push, merge ir reset, išskleidimo, žinučių siuntimo, mokėjimų ir nustatymų keitimo | Išsaugotų slaptų duomenų skaitymas, saugos nustatymų keitimas ir bet koks veiksmas, kurio poveikio VOOL negali nustatyti |
| Leidimų apėjimas | Viskas, išskyrus keturias apsaugotas klases | Dėl mokėjimų | Išsaugotų slaptų duomenų skaitymas, saugos nustatymų keitimas ir bet koks veiksmas, kurio poveikio VOOL negali nustatyti |

Režimas, įgūdis ar papildinys gali tik susiaurinti tai, kas siūloma. Nė vienas iš jų nesuteikia veiksmo,
kurio režimas neleidžia.

## Atsakymas į leidimo užklausą

Kai veiksmui reikia jūsų sprendimo, virš žinutės laukelio atsidaro juosta **Reikalingas leidimas**. Joje
nurodomas tikslus veiksmas: komandos eilutė, kuri bus paleista, arba failai, kurie pasikeis, ar pakeitimą
galima atšaukti, ir redagavimų peržiūra. Niekas nevykdoma, kol neatsakote, o užklausa jūsų laukia net ir
iš naujo paleidus VOOL. Užklausa, kurios laikas baigėsi, rodoma kaip nebegaliojanti, ir jos negalima
patvirtinti.

Juosta siūlo tik tuos pasirinkimus, kurie tinka veiksmui:

| Mygtukas | Ką leidžia |
| --- | --- |
| Leisti vieną kartą | Šį vieną tikslų veiksmą. |
| Leisti šiai užduočiai | Tą patį tikslų veiksmą dar kartą šios užduoties metu, tuo pačiu režimu. Siūloma veiksmams, kurie nėra failų redagavimas. |
| Leisti visus *N* planuojami pakeitimai šiai užklausai | Failų pakeitimus, kuriuos ši užklausa jau suplanavo, tiksliai tuos failus, kurie išvardyti skiltyje „Peržiūrėti išsamiai“, ir nieko daugiau. |
| Leisti darbo aplankos keitimus šiame pokalbyje | Skaitymus, naujus failus ir redagavimus šio pokalbio darbo srityje aštuonias valandas arba kol VOOL bus paleistas iš naujo ar pokalbis ištrintas. |
| Leisti šiame projekte | Skaitymus ir failų rašymus projekto aplanke. Tai galioja projektui, taip pat ir po paleidimo iš naujo bei naujuose pokalbiuose. |
| Peržiūrėti išsamiai | Prieš jums apsisprendžiant parodo visą užklausą. |
| Atmesti | Veiksmas neįvykdomas. |

Kad ir ką leistumėte, dėl trynimų, perkėlimų, perrašymų, komandų, tinklo veiksmų, slaptų duomenų ir
mokėjimų vis tiek kaskart klausiama atskirai, nebent juos leidžia pats režimas.

## Leidimų apėjimas

Apėjimas paleidžia prieinamus įrankius be atskirų patvirtinimų, jūsų pasirinktoje apimtyje ir jūsų
pasirinktam laikui:

* **Apimtis:** tik dabartinė užduotis, ši pokalbio sesija arba šis projektas.
* **Trukmė:** 15 arba 30 minučių, 1, 2, 4 arba 8 valandos, pasirinktinė trukmė nuo 15 iki 1440 minučių
  arba *Kol pats išjungsiu*, tik šiam pokalbiui.

Laikinas apėjimas veikia bet kuriame pokalbyje. *Kol pats išjungsiu* reikalauja, kad pokalbis būtų
susietas su projekto aplanku, ir tas aplankas negali būti visas jūsų namų aplankas, disko šaknis ar
aplankas, kuriame yra paties VOOL duomenys. Kol apėjimas įjungtas, pranešimo juosta rodo jo apimtį ir
pabaigos laiką, o jos **×** iš karto jį išjungia; pokalbis grįžta į režimą Rankinis. Ištrynus pokalbį,
jo apėjimas taip pat baigiasi. Apėjimas niekada nesuteikia prieigos už darbo srities ribų ir negali leisti
modeliui išplėsti savo leidimų. Dėl mokėjimų vis tiek klausiama, o slapti duomenys ir saugos nustatymai
lieka nepasiekiami.

## Mokėjimai ir išlaidos

Veiksmo patvirtinimas niekada nepatvirtina mokėjimo. Mokėjimai bet kuriuo režimu eina per paties
piniginės pasiūlymą ir patvirtinimą, su jos limitais. Mokamus modelių iškvietimus jūsų išlaidų limitai ir
kainos apsaugos sulaiko dar prieš susisiekiant su tiekėju. Žr. [Piniginė](../guides/wallet.md) ir
[Išlaidų ribos ir kainos apsaugos](../guides/spending-limits.md).

## Kai veiksmas atmetamas

Atmestas veiksmas nieko nepakeitė, o pranešime nurodoma, kas atmesta ir kodėl. Kodai, kuriuos galite
pamatyti: [`permission_denied`](../ERROR_BOOK.md#permission-denied),
[`confinement_refusal`](../ERROR_BOOK.md#confinement-refusal), kai pakeitimas būtų už leistino aplanko
ribų, ir [`tool_unavailable`](../ERROR_BOOK.md#tool-unavailable), kai įrankio šis režimas nesiūlo.
Žr. [Klaidos ir klaidų kodai](../reference/errors.md).

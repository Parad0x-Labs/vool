---
description: Kaip VOOL atsinaujina, kas nutinka jūsų duomenims ir koks yra versijų suderinamumas.
translation: machine
source_sha256: 3b6a950556f4669f116dfa3a911ffb5e07ad8bcfa04415d68cf5a3d1adf0fe65
---

# Atnaujinimai ir migracija

## Kaip veikia atnaujinimai

Nuo 0.7.5 VOOL atsinaujina pats: įjungus „Prisijungti prie interneto savarankiškai“, jis parodo pranešimą ir vienu paspaudimu įdiegia naują versiją. Atnaujinant iš 0.7.4 ar 0.7.3 Mac kompiuteryje: įjunkite „Prisijungti prie interneto savarankiškai“ (Nustatymai > Privatumas ir leidimai) ir vieną kartą paspauskite „Tikrinti atnaujinimus“. Iš 0.7.4 ar senesnės Linux sistemoje arba iš 0.7.2 ar senesnės Mac kompiuteryje: atsisiųskite 0.7.7 ir įdiekite ją ant dabartinės versijos. Jūsų pokalbiai ir nustatymai saugomi jūsų profilyje, už programos ribų.

Įjungus „Prisijungti prie interneto savarankiškai“, VOOL naujos versijos ieško paleidžiamas ir kartą per
dieną, o pranešimą parodo jums nespaudžiant „Tikrinti atnaujinimus“. Kai šis nustatymas išjungtas, VOOL
nieko neatsisiunčia, o antraštėje rodoma **Atnaujinimų tikrinimas išjungtas**.

Pasirašytas atnaujinimo paketas pereina patikrintą procesą su automatiniu atšaukimu:

1. **Atsisiuntimas** — patikrinamas pagal pasirašytą manifestą.
2. **Parengtinis diegimas** — nauja versija įdiegiama šalia senosios.
3. **Migracija** — jūsų duomenys perkeliami į naują saugyklos versiją. Jei migracija nepavyksta, jūsų
   duomenys atkuriami į ankstesnę versiją, o programa iš naujo paleidžiama su ja: *„Your data was
   restored to the previous version.“*
4. **Būklės patikra** — nauja versija turi įrodyti, kad gali aptarnauti užklausas, prieš išjungiant senąją.
5. **Sukeitimas** — atominis; ankstesnė versija išlaikoma kaip atšaukimo tikslas.

Linux sistemoje įdiegimas iš tar archyvo atnaujinamas taip pat. Nauja versija įdiegiama šalia veikiančios,
o `current` persijungia į ją vienu žingsniu. Jei nauja versija neveikia tinkamai, VOOL pats grįžta prie
ankstesnės. Rankinis mygtukas **Grįžti į …** siūlomas Mac kompiuteryje ir įdiegimams iš šaltinio kodo, bet
kol kas ne Linux tar archyvo įdiegimui.

Jei kuris nors etapas nepavyksta, programa paliekama tokia, kokia buvo — nesėkmės pranešime įvardijamas
etapas (`verification_failed`, `insufficient_disk_space`, …), o kiekvienas etapas turi savo sakinį apie
tai, kaip atkurti veikimą.

## Duomenų suderinamumas

* Jūsų profilis, pokalbiai, kvitai ir nustatymai laikomi už programos paketo ribų, todėl pakeitus programą
  jūsų duomenys niekada nepakeičiami.
* Vietinė saugykla atsisako atsidaryti po **grįžimo prie senesnės versijos**: naujesnio VOOL įrašytos
  duomenų bazės senesnis VOOL tyliai neskaitys — jis tai praneša ir atsisako, nes spėjant nežinomą
  saugyklos struktūrą ją būtų galima sugadinti. Norėdami skaityti naujus duomenis, iš naujo įdiekite
  naujesnę versiją.
* Atnaujinimo įdiegti failai ir pati atnaujinimo būsena įrašomi jūsų profilyje kartu su kvitais, todėl į
  klausimą „kuri versija keitė mano duomenis“ galima atsakyti ir vėliau.

## Beta kanalas

Dabartinė leidimų linija yra **beta** (žr. [Leidimų būsena](../trust/release-status.md)).
Atnaujinimai pasirašomi prisegtu leidėjo raktu ir patikrinami prieš diegimą. Pati macOS programa pasirašyta
ad hoc ir nėra notarizuota, todėl macOS gali paprašyti jūsų patvirtinti pirmąjį naujos versijos paleidimą.

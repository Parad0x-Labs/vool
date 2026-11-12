---
description: Įrankiai yra veiksmai, kuriuos gali atlikti VOOL, ir kiekvienas iš jų įrašomas.
translation: machine
source_sha256: 53722a7f30f4446b6d029465a402a823f60496a66008b5df150d201c1963b152
---

# Įrankiai

Įrankis yra veiksmas, kurį vykdymo aplinka gali atlikti jūsų vardu — perskaityti failą, paleisti
komandą, ieškoti aplanke.

## Patikrinami iš esmės

Kiekvienas įrankio iškvietimas įrašo, ko buvo paprašyta, kas buvo paleista ir kas grįžo. Vykdymą galima
vėliau perskaityti jo nepaleidžiant iš naujo.

## Leidimai

Ar įrankis paleidžiamas iš karto, ar pirmiausia klausia, priklauso nuo jūsų pasirinkto režimo ir nuo veiksmo.
Naujas pokalbis pradedamas režimu Rankinis, kuriame dėl kiekvieno pakeitimo pirmiausia klausiama. Režimu
Automatinis failų kūrimas ir redagavimas, komandos ir tinklo veiksmai vykdomi neklausiant, o dėl trynimo,
perrašymo, diegimo, Git commit ir push operacijų, išskleidimo, žinučių siuntimo, mokėjimų ir nustatymų
keitimo vis tiek klausiama. Bet kuriuo režimu failų įrankiai veikia tik [darbo srityje](workspaces.md),
paieškos ir parsiuntimo įrankiai skaito viešus išteklius nieko neįrašydami, o išsaugoti slapti duomenys
niekada neskaitomi. Puslapyje [Leidimai](permissions.md) aprašyti visi penki režimai ir tai, ką leidžia
kiekvienas patvirtinimo mygtukas.

```text
tool: read_file
path: src/main.py
result: 412 lines
```

Taip pat žr. [Kvitai](receipts.md).

---
description: Kaip įdiegti DI asistentą VOOL macOS (Apple Silicon arba Intel) ar Linux sistemoje iš leidimo atsisiuntimų arba iš šaltinio kodo macOS, Windows ar Linux sistemoje, įskaitant sistemos reikalavimus ir atsisiuntimo patikrą.
translation: machine
source_sha256: e0025d89fc56caca91923de6f1e6afe25ba6f6383a300ec3df5a7f7e3f401e55
---

# Diegimas

VOOL 0.7.7-beta programą Mac kompiuteriams su Apple Silicon, Mac kompiuteriams su Intel ir Linux x86_64 galima atsisiųsti iš
[leidimo v0.7.7-beta](https://github.com/Parad0x-Labs/vool/releases/tag/v0.7.7-beta).
Taip pat galite įdiegti iš šaltinio kodo naudodami paleidimo scenarijų macOS, Linux arba Windows sistemoje.
Apie pasirašymą ir atnaujinimus skaitykite skyriuje [Leidimų būsena](../trust/release-status.md).

## Reikalavimai

| | Mažiausiai | Patogiai |
| --- | ---: | ---: |
| Operatyvioji atmintis | 8 GB | 16 GB ar daugiau |
| Diskas | 2 GB programai | 20 GB su vietiniais modeliais |
| macOS | 14 Sonoma, Apple Silicon arba Intel ([apribojimai](#mac-kompiuteriai-su-intel)) | 14 Sonoma ar naujesnė, Apple Silicon |
| Windows | 10 (64 bitų) | 11 |
| Linux | glibc 2.31 | Ubuntu 22.04 ar naujesnė |

Vietiniams modeliams reikia gerokai daugiau operatyviosios atminties nei pačiai programai. Patarimai dėl dydžio pateikti skyriuje
[Vietinio modelio paleidimas](../guides/local-models.md).

## Programos atsisiuntimas

| Jūsų kompiuteris | Atsisiuntimas |
| --- | --- |
| Mac su Apple Silicon (M1 ir naujesni) | [`VOOL-0.7.7-beta-macos-arm64.dmg`](https://github.com/Parad0x-Labs/vool/releases/download/v0.7.7-beta/VOOL-0.7.7-beta-macos-arm64.dmg) |
| Mac su Intel procesoriumi | [`VOOL-0.7.7-beta-macos-x86_64.dmg`](https://github.com/Parad0x-Labs/vool/releases/download/v0.7.7-beta/VOOL-0.7.7-beta-macos-x86_64.dmg) |
| Linux x86_64 (Ubuntu 22.04 / 24.04) | [`vool-0.7.7-beta-linux-x86_64.tar.gz`](https://github.com/Parad0x-Labs/vool/releases/download/v0.7.7-beta/vool-0.7.7-beta-linux-x86_64.tar.gz) |

macOS neturi lietuviškos sąsajos, todėl toliau „Apple“ meniu ir mygtukų pavadinimai pateikiami angliškai, kaip juos rodo jūsų Mac.
Norėdami sužinoti, kokį procesorių turi jūsų Mac, atidarykite „Apple“ meniu (Apple menu) ir pasirinkite **About This Mac**. „Apple M…“
reiškia Apple Silicon, „Intel“ — Intel. Programa, skirta Apple Silicon, neveikia Mac kompiuteryje su Intel
ir tai praneša atidarant.

Prieš atidarydami patikrinkite atsisiųsto failo SHA-256 (žr. [Atsisiuntimo patikra](#atsisiuntimo-patikra)).

### Pirmasis atidarymas Mac kompiuteryje

Beta programa dar nėra notarizuota Apple, todėl pirmą kartą ją atidarant macOS paprašys patvirtinimo.
Taip ir turi būti, ir tai nutinka tik vieną kartą.

1. Atidarykite DMG failą ir nuvilkite **VOOL** į **Applications**.
2. Dukart spustelėkite VOOL. macOS praneša, kad negali patikrinti, ar programoje nėra kenkėjiškos programinės įrangos.
   Uždarykite pranešimą.
3. Atidarykite **System Settings → Privacy & Security**. Slinkite žemyn iki pranešimo apie VOOL
   ir spustelėkite **Open Anyway**. Patvirtindami įveskite slaptažodį.

Sistemoje macOS 14 Sonoma galite vietoj to aplanke Applications spustelėti VOOL laikydami nuspaudę Control, pasirinkti
**Open**, tada dar kartą spustelėti **Open**.

### Linux

Iš leidimo atsisiųskite tar archyvą ir jo `.sha256` failą, patikrinkite archyvą, tada paleiskite
kartu pateiktą diegimo programą. Nereikia nei sudo, nei sistemos Python.

```bash
sha256sum -c vool-0.7.7-beta-linux-x86_64.tar.gz.sha256
tar -xzf vool-0.7.7-beta-linux-x86_64.tar.gz
bash vool-0.7.7-beta-linux-x86_64/install.sh
~/.local/bin/vool
```

VOOL įsidiegia į `~/.local/share/vool`, komandą `vool` įdeda į `~/.local/bin` ir prideda
įrašą programų meniu. Darbalaukio versija atidaro pokalbį jūsų naršyklėje; uždarius naršyklę,
VOOL nesustoja. Norėdami pašalinti VOOL, paleiskite `bash ~/.local/share/vool/current/uninstall.sh`.
Jūsų pokalbiai ir nustatymai lieka jūsų profilyje.

Nuo 0.7.5 Linux versija atnaujinama pačiame VOOL. Nauja versija įdiegiama šalia
senosios. Jei ji tinkamai nepasileidžia, VOOL pats grįžta prie ankstesnės.

### Po diegimo

Kad vietinis modelis galėtų atsakyti, atidarykite **Nustatymai → Modeliai ir tiekėjai → Vietiniai modeliai** ir vieną kartą paspauskite jam **Certify**. Taip jūsų kompiuteryje paleidžiamas trumpas patikrinimas.

Nuo 0.7.5 VOOL atsinaujina pats: įjungus „Prisijungti prie interneto savarankiškai“, jis parodo pranešimą ir vienu paspaudimu įdiegia naują versiją. Atnaujinant iš 0.7.4 ar 0.7.3 Mac kompiuteryje: įjunkite „Prisijungti prie interneto savarankiškai“ (Nustatymai > Privatumas ir leidimai) ir vieną kartą paspauskite „Tikrinti atnaujinimus“. Iš 0.7.4 ar senesnės Linux sistemoje arba iš 0.7.2 ar senesnės Mac kompiuteryje: atsisiųskite 0.7.7 ir įdiekite ją ant dabartinės versijos. Jūsų pokalbiai ir nustatymai
saugomi jūsų profilyje, už programos ribų. Kriptovaliutų funkcijos pagal numatytuosius nustatymus išjungtos.

## Diegimas iš šaltinio kodo

Paleiskite savo platformai skirtą paleidimo scenarijų:

```bash
curl -fsSLo bootstrap_vool.sh https://raw.githubusercontent.com/Parad0x-Labs/vool/v0.7.7-beta/installer/bootstrap_vool.sh
bash bootstrap_vool.sh --ref v0.7.7-beta
```

```powershell
Invoke-WebRequest https://raw.githubusercontent.com/Parad0x-Labs/vool/main/installer/bootstrap_vool.ps1 -OutFile bootstrap_vool.ps1
powershell -ExecutionPolicy Bypass -File .\bootstrap_vool.ps1
```

macOS / Linux komandos prie `v0.7.7-beta` pririša ir paleidimo scenarijų, ir jo šaltinio kodo atsisiuntimą. Norėdami pasirinkti kitą leidimą, pakeiskite ir žymę scenarijaus URL, ir `--ref`. Paleidimo scenarijus priima šakos pavadinimą arba leidimo žymę, bet ne įsipareigojimo maišą.

Diegiant iš šaltinio kodo reikia Python 3.11 ar naujesnio. Jei jo nerandama, diegimo programa bando
jį parengti. Programos atsisiuntimuose yra savas Python.

Diegimo programa paruošia Python aplinką, patikrina jūsų aparatinę įrangą, parengia vietinį
modelį ir paleidžia vietines paslaugas. Numatykite laiko ir vietos diske pirmajam modelio atsisiuntimui.
Po parengimo vietinių modelių išvedimas veikia be interneto; žiniatinklio įrankiams ir debesijos modeliams reikia
ryšio.

## Mac kompiuteriai su Intel

VOOL veikia Mac kompiuteriuose su Intel ir macOS 14 ar naujesne (pavyzdžiui, 2020 m. 13 colių MacBook Pro),
iš Intel atsisiuntimo arba iš šaltinio kodo. Diegimo iš šaltinio kodo programa aptinka Intel procesorių ir
įdiegia visų priklausomybių Intel versijas. Du apribojimai būdingi tik Intel:

* **Nėra vietinių modelių mokymo.** PyTorch nuo 2.3 nebeskelbia versijų macOS sistemai su Intel, todėl
  diegimo programa jį praleidžia, o LoRA mokymas praneša, kad yra nepasiekiamas. Pokalbiai, atmintis,
  įrankiai ir debesijos modeliai nepaveikiami.
* **Vietiniai modeliai veikia tik CPU.** Vietinių modelių paleidimo programos neturi GPU spartinimo Intel
  grafikai, todėl vietiniai atsakymai kelis kartus lėtesni nei su Apple Silicon. Mac kompiuteryje su 8 GB
  pokalbiams naudokite debesijos modelį; su 16 GB numatytasis mažas diegimo programos vietinis modelis veikia,
  bet lėtai.

Mac kompiuteriuose su Intel vietiniai modeliai veikia procesoriuje ir yra lėti. Ilgas atsakymas iš atminties gali viršyti laukimo laiką, todėl kol kas ten naudokite debesijos modelį. Vietinių modelių sertifikavimas atsižvelgia į lėtesnius procesorius.

Mac kompiuteriai su Intel turi atskirą atsisiuntimą, `VOOL-0.7.7-beta-macos-x86_64.dmg`. Apple Silicon atsisiuntimas
neveikia su Intel ir paleidžiant tai praneša.

## Atsisiuntimo patikra

Prie kiekvieno leidimo atsisiuntimo yra SHA-256 kontrolinė suma. Prieš atidarydami failą
jį patikrinkite:

```bash
shasum -a 256 <downloaded-artifact>    # macOS
sha256sum <downloaded-artifact>         # Linux
```

```powershell
Get-FileHash -Algorithm SHA256 <downloaded-artifact>
```

Palyginkite rezultatą su kontroline suma, paskelbta kartu su tuo leidimu. Jei jos skiriasi,
ištrinkite failą ir atsisiųskite jį iš naujo.

## Toliau

Tęskite skyriuje [Pirmasis paleidimas](first-run.md).

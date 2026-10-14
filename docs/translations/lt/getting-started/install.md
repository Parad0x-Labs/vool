---
description: Atsisiųskite ir įdiekite VOOL 0.7.0-beta macOS (Apple silicon arba Intel) ar Linux sistemoje. Windows palaikymas eksperimentinis, diegiama iš šaltinio kodo.
---

# Diegimas

## Reikalavimai

| | Minimalu | Patogu |
| --- | --- | ---: |
| Atmintis | 8 GB | 16 GB ar daugiau |
| Diskas | 2 GB programai | 20 GB su vietiniais modeliais |
| macOS | 14 Sonoma, Apple silicon arba Intel | 14 Sonoma ar naujesnė, Apple silicon |
| Linux | x86_64, glibc 2.31 | Ubuntu 22.04 arba 24.04 |
| Windows | Eksperimentinis, tik iš šaltinio kodo | |

## Atsisiuntimas

Visi failai yra [v0.7.0-beta leidime](https://github.com/Parad0x-Labs/vool/releases/tag/v0.7.0-beta), prie kiekvieno paskelbta SHA-256 kontrolinė suma.

| Jūsų kompiuteris | Atsisiųsti |
| --- | --- |
| Mac su Apple silicon (M1 ar naujesnis) | [`VOOL-0.7.0-beta-macos-arm64.dmg`](https://github.com/Parad0x-Labs/vool/releases/download/v0.7.0-beta/VOOL-0.7.0-beta-macos-arm64.dmg) |
| Mac su Intel procesoriumi | [`VOOL-0.7.0-beta-macos-x86_64.dmg`](https://github.com/Parad0x-Labs/vool/releases/download/v0.7.0-beta/VOOL-0.7.0-beta-macos-x86_64.dmg) |
| Linux x86_64 | [`vool-0.7.0-beta-linux-x86_64.tar.gz`](https://github.com/Parad0x-Labs/vool/releases/download/v0.7.0-beta/vool-0.7.0-beta-linux-x86_64.tar.gz) |

Nežinote, koks jūsų Mac? Apple meniu pasirinkite **About This Mac**: „Apple M…" reiškia Apple silicon, „Intel" reiškia Intel.

Intel Mac kompiuteriuose vietiniai modeliai veikia tik procesoriuje ir gali būti per lėti, kad praeitų vienkartinį Certify patikrinimą ar spėtų atsakyti laiku, todėl kol kas ten naudokite debesų modelį. Intel versiją išbandėme per Rosetta Apple silicon kompiuteryje, Intel Mac kompiuteryje dar ne.

## Pirmas atidarymas macOS

1. Atidarykite DMG ir nutempkite **VOOL** į `Applications`.
2. Dukart spustelėkite VOOL. Versija dar nenotarizuota Apple, todėl macOS praneš, kad negali patikrinti programos. Spauskite **Done**.
3. Atidarykite **System Settings → Privacy & Security**, slinkite žemyn iki VOOL įrašo ir pasirinkite **Open Anyway**. Patvirtinkite slaptažodžiu.

macOS 14 sistemoje taip pat galite spustelėti VOOL laikydami Control ir pasirinkti **Open**. Tai reikia padaryti tik kartą.

## Vietiniai modeliai

Prieš naudodami vietinį modelį, atidarykite **Settings → Models & Providers → Local models** ir vieną kartą paspauskite **Certify**. 0.7.0 versija apie tai nepraneša; aiškesnis raginimas bus 0.7.1 versijoje.

## Linux

Atsisiųskite archyvą ir jo `.sha256` failą, patikrinkite ir paleiskite pridėtą diegimo programą. Sudo nereikia:

```bash
sha256sum -c vool-0.7.0-beta-linux-x86_64.tar.gz.sha256
tar -xzf vool-0.7.0-beta-linux-x86_64.tar.gz
bash vool-0.7.0-beta-linux-x86_64/install.sh
```

## Patikrinkite atsisiuntimą

```bash
shasum -a 256 VOOL-0.7.0-beta-macos-arm64.dmg        # macOS
sha256sum vool-0.7.0-beta-linux-x86_64.tar.gz   # Linux
```

Palyginkite rezultatą su kontroline suma leidimo puslapyje. Jei nesutampa, ištrinkite failą ir atsisiųskite iš naujo.

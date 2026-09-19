---
description: Atsisiųskite ir įdiekite VOOL macOS, Windows arba Linux sistemoje.
---

# Diegimas

## Reikalavimai

| | Minimalu | Patogu |
| --- | --- | ---: |
| Atmintis | 8 GB | 16 GB ar daugiau |
| Diskas | 2 GB programai | 20 GB su vietiniais modeliais |
| macOS | 14 Sonoma, Apple silicon | 14 Sonoma ar naujesnė |
| Windows | 10 (64 bitų) | 11 |
| Linux | glibc 2.31 | Ubuntu 22.04 ar naujesnė |

## macOS

1. Atsisiųskite `VOOL-0.6.0-beta-cffc5c3-macos-arm64.dmg` iš [vool.dev](https://vool.dev/#cta). Programa veikia Apple silicon
   kompiuteriuose (M1 ar naujesniuose) su macOS 14 Sonoma ar naujesne.
2. Atidarykite ir nutempkite **VOOL** į `Applications`.
3. Pirmą kartą macOS atsisakys paleisti, nes versija nenotarizuota. Atidarykite **System Settings → Privacy & Security**, raskite VOOL įrašą ir pasirinkite **Open Anyway**.

## Patikrinkite atsisiuntimą

```bash
shasum -a 256 VOOL-0.6.0-beta-cffc5c3-macos-arm64.dmg
```

Palyginkite rezultatą su kontroline suma, paskelbta šalia diegimo failo:
[VOOL-0.6.0-beta-cffc5c3-macos-arm64.dmg.sha256](https://vool.dev/downloads/VOOL-0.6.0-beta-cffc5c3-macos-arm64.dmg.sha256).

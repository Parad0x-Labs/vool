---
description: Dažniausių problemų sprendimai.
translation: machine
source_sha256: 792c90fd68caf7409212d3742109c46e49d1f7e640aa3e9f70db9fe61ca55d77
---

# Problemų sprendimas

## macOS neatidaro programos

Taip ir turi būti: programa dar nėra notarizuota. Pirmiausia patikrinkite kontrolinę sumą, tada atidarykite
**System Settings → Privacy & Security** ir spustelėkite **Open Anyway**. Sistemoje macOS 14 galite vietoj to
spustelėti VOOL laikydami nuspaudę Control ir pasirinkti **Open**.

## Windows

Šiame leidime nėra Windows atsisiuntimo. Windows sistemoje diegiama iš šaltinio kodo, ir šis palaikymas
eksperimentinis; žr. [diegimo vadovą](../getting-started/install.md#diegimas-iš-šaltinio-kodo).

## Nerodomas nė vienas vietinis modelis

1. Įsitikinkite, kad vykdymo aplinka veikia.
2. Įsitikinkite, kad atsisiųstas bent vienas modelis.
3. Įsitikinkite, kad prievadas atitinka tą, kurio tikisi VOOL.

```bash
curl http://localhost:11434/api/tags
```

Jei ši komanda nieko negrąžina, vykdymo aplinka neveikia.

## Atsakymai lėti

Paprastai tai lemia modelio dydis, palyginti su turima operatyviąja atmintimi. Žr.
[Vietinio modelio paleidimas](../../../guides/local-models.md).

## Pradėti iš naujo

Uždarykite VOOL ir pašalinkite konfigūracijos katalogą, nurodytą skyriuje
[Konfigūracija](../../../reference/configuration.md). Darbo srities failai nepaliečiami.

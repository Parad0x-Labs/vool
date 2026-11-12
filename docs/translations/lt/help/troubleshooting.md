---
description: Dažniausių problemų sprendimai.
translation: machine
source_sha256: 809bae11bc4bf8c2d707cfef616f80056a5d98d35b0786cc937a40526e802689
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

## Klaidoje rodomas kodas

Kiekviena nesėkmė turi pastovų kodą. Puslapyje [Klaidos ir klaidų kodai](../../../reference/errors.md)
paaiškinta, kaip jį perskaityti, o [Klaidų knygoje](../../../ERROR_BOOK.md) prie kiekvieno kodo nurodyta,
kas nutiko, kas galbūt jau įvyko ir ką daryti toliau.

## Pradėti iš naujo

Uždarykite VOOL ir pašalinkite aplanką „runtime home“, nurodytą skyriuje
[Konfigūracija](../../../reference/configuration.md). Taip ištrinsite savo pokalbius, atmintį, nustatymus,
išsaugotus raktus ir bet kurią piniginę, kurią VOOL laiko šiame kompiuteryje, todėl pirmiausia
išsisaugokite tai, ko reikia: žr. [Atsarginės kopijos ir atkūrimas](../../../help/backup-and-recovery.md). Darbo
srities failai nepaliečiami.

---
description: Kaip su VOOL paleisti DI modelius vietoje savo kompiuteryje — vietinio modelių serverio diegimas, modelio dydžio pasirinkimas ir darbas visiškai be interneto.
translation: machine
source_sha256: d93b9995b0f4fec8aca12bb90d8d6f9ff0fd9623c43d4529913dc76f055ac24c
---

# Vietinio modelio paleidimas

## Vykdymo aplinkos diegimas

VOOL bendrauja su vietiniu modelių serveriu. macOS programoje yra sava vietinių modelių paleidimo programa,
todėl Mac kompiuteryje šį žingsnį galite praleisti. Linux sistemoje ir diegdami iš šaltinio kodo įdiekite
serverį, pavyzdžiui, Ollama, paleiskite jį ir atsisiųskite modelį.

```bash
ollama serve
ollama pull qwen2.5-coder:7b
```

## VOOL nukreipimas į jį

Atidarykite **Nustatymai → Modeliai ir tiekėjai**. Veikiančios vietinės vykdymo aplinkos aptinkamos
automatiškai. Pasirinkite modelį, ir režimo indikatorius pasikeis į Lokaliai.

## Vienkartinis sertifikavimas

Vietinis modelis rašo atsakymus tik po to, kai yra sertifikuotas. Atidarykite **Nustatymai → Modeliai ir tiekėjai → Vietiniai modeliai**,
užregistruokite modelį, tada pasirinkite **Certify**. Sertifikuojant jūsų kompiuteryje paleidžiamas trumpas
įrankių patikrinimas; niekas neatsisiunčiama ir su niekuo nesusisiekiama. Kol modelis nepraeina patikrinimo,
VOOL neleidžia jam rašyti galutinių atsakymų.

## Dydžio parinkimas

| Modelio dydis | Reikalinga operatyvioji atmintis | Tinka |
| --- | ---: | --- |
| 3B | ~4 GB | Trumpiems redagavimams, greitiems klausimams |
| 7–8B | ~8 GB | Bendram naudojimui, daugumai programavimo užduočių |
| 14B | ~16 GB | Ilgesniam samprotavimui |
| 32B+ | 32 GB ar daugiau | Sunkesniam darbui, lėčiau |

{% hint style="info" %}
Kvantuotos versijos gerokai sumažina operatyviosios atminties poreikį. 7B modelis su 4 bitų kvantavimu
patogiai veikia maždaug su 5 GB.
{% endhint %}

## Jei veikia lėtai

* Pasirinkite mažesnį arba labiau kvantuotą modelį.
* Uždarykite kitas daug atminties naudojančias programas.
* Sutrumpinkite kontekstą — mažiau failų užklausoje reiškia mažiau apdorojimo.

---
description: Kaip su VOOL paleisti DI modelius vietoje savo kompiuteryje — vietinio modelių serverio diegimas, modelio dydžio pasirinkimas ir darbas visiškai be interneto.
translation: machine
source_sha256: a19d4790729d13ed5e1b134bcb67ed3e7a80609561be4227ab9480492100c8a6
---

# Vietinio modelio paleidimas

## Vykdymo aplinkos diegimas

Vietiniams modeliams paleisti VOOL numatytai naudoja Ollama; tinka ir kitos vietinės vykdymo aplinkos su
OpenAI suderinamu galiniu tašku. Ollama yra atskira programa, diegiama atskirai
iš [ollama.com](https://ollama.com). macOS programoje yra sava vietinių modelių paleidimo programa,
todėl Mac kompiuteryje šį žingsnį galite praleisti. Linux sistemoje ir diegdami iš šaltinio kodo įdiekite
Ollama, paleiskite ją ir atsisiųskite modelį.

```bash
ollama serve
ollama pull qwen3:4b
```

VOOL numatytai pats parenka jums vietinį modelį — Qwen modelį, pritaikytą jūsų kompiuteriui. Daugelyje
kompiuterių tai `qwen3:4b`; kompiuteriuose, turinčiuose 32 GB operatyviosios atminties ar daugiau, numatytasis
yra didesnis Qwen modelis.

## VOOL nukreipimas į jį

Atidarykite **Nustatymai → Modeliai ir tiekėjai**. Veikiančios vietinės vykdymo aplinkos aptinkamos
automatiškai. Pasirinkite modelį, ir režimo indikatorius pasikeis į Lokaliai.

## Vienkartinis parengimas

Vietinis modelis rašo atsakymus tik po to, kai VOOL jį patikrina. Atidarykite
**Nustatymai → Modeliai ir tiekėjai → Vietiniai modeliai** ir paspauskite **Naudoti šį modelį** šalia modelio.
VOOL tyliai jūsų kompiuteryje patikrina, ar modelis pakankamai gerai naudoja VOOL įrankius, kad galėtų atsakyti
už jus. Patikrinimas gali užtrukti minutę ar dvi; niekas neatsisiunčiama ir su niekuo nesusisiekiama. Kai
patikrinimas išlaikytas, prie modelio rodoma **Paruošta**, ir VOOL atsako juo. Iki tol VOOL neleidžia jam rašyti
galutinių atsakymų.

* **Netinka:** modelis nepakankamai gerai naudoja VOOL įrankius. VOOL nurodo, ką galite padaryti vietoj to:
  naudoti arba atsisiųsti modelį, kurį jis parinko šiam kompiuteriui, arba naudoti debesijos modelį.
* **Patikrinimas nebaigtas:** įsitikinkite, kad Ollama atidaryta, tada paspauskite **Bandyti dar kartą**. Lėtam
  kompiuteriui gali prireikti antro bandymo.

Jei paleidžiant VOOL joks vietinis modelis nėra paruoštas, jis patikrina iki trijų šio kompiuterio modelių,
pradėdamas nuo jūsų pasirinktojo, ir naudoja pirmąjį, kuris išlaiko patikrinimą. Šiam kompiuteriui per didelį
modelį jis praleidžia. Paruoštas modelis visada eina pirma neparuošto.

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

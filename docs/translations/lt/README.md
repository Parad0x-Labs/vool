---
description: VOOL yra darbalaukio DI asistentas, kuris DI modelius paleidžia vietoje jūsų kompiuteryje arba jungiasi prie debesijos modelių su jūsų pačių raktu. Nemokamas ir privatus. Atsisiuntimai Mac (Apple Silicon ir Intel) ir Linux kompiuteriams; Windows palaikymas eksperimentinis, diegiama iš šaltinio kodo.
translation: machine
source_sha256: dc6715130dd5dcfe7a6adc926aeff095413e59b7eecc1735d1c2ee1d88990ac5
---

# VOOL

VOOL yra darbalaukio DI asistentas. Jis įdiegiamas jūsų kompiuteryje kaip programa ir gali
paleisti DI modelius vietoje tame kompiuteryje arba prisijungti prie debesijos DI tiekėjo su jūsų pačių
API raktu. Jis saugo skaitomą kiekvieno savo atlikto veiksmo įrašą.

Jis nemokamas. Su vietiniu modeliu jūsų pokalbiams ir failams nereikia interneto ryšio; naujausia informacija,
pavyzdžiui, orai ar kainos, gaunama iš viešųjų žiniatinklio paslaugų tik tada, kai įjungta „Interneto paieška“.
Yra atsisiuntimai Mac kompiuteriams (Apple Silicon ir Intel) ir Linux x86_64; Windows palaikymas
eksperimentinis, diegiama iš šaltinio kodo.

Šioje dokumentacijoje aprašyta, kaip įdiegti VOOL ir prijungti modelį, taip pat principai, kaip jis
tvarko jūsų failus, jūsų atmintį ir jūsų raktus.

## Kaip jam sekasi

Su 150 niekada anksčiau nenaudotų LoCoMo atminties klausimų, kai už kiekvieną agentą atsakė tas pats modelis,
o vertino tas pats GPT-4o, VOOL atmintis aplenkė du kitus atvirojo kodo agentus:

| | VOOL | OpenClaw | Hermes |
| --- | --- | --- | --- |
| Teisingi atsakymai | **82,7 %** (124 / 150) | 71,3 % (107) | 62,7 % (94) |
| Žetonai vienam klausimui | **9,6k** | 51,8k | 33,5k |
| Vieno klausimo kaina | **0,0033 USD** | 0,0100 USD | 0,0046 USD |

Tai 5,4 karto mažiau žetonų nei OpenClaw ir 3,5 karto mažiau nei Hermes, o atsakymo kaina siekia trečdalį
OpenClaw kainos. VOOL 0.7 pateikta atmintis tais pačiais klausimais surinko 123 iš 150.
Kiekvienas paleidimas, sąlygos ir silpnosios vietos paskelbti: [atminties lyginamieji testai](https://github.com/Parad0x-Labs/vool/tree/main/docs/benchmarks).

## Pradėkite čia

| Jei norite | Skaitykite |
| --- | --- |
| Paleisti VOOL | [Diegimas](getting-started/install.md) |
| Suprasti pirmąjį ekraną | [Pirmasis paleidimas](getting-started/first-run.md) |
| Nurodyti jam modelį | [Modelio prijungimas](getting-started/connect-a-model.md) |
| Valdyti, kokios išlaidos gali atsirasti | [Išlaidų ribos](guides/spending-limits.md) |
| Žinoti, kas palieka jūsų kompiuterį | [Vietoje ir debesijoje](concepts/local-and-cloud.md) |

## Kas yra VOOL

* **Darbalaukio programa.** Ji įdiegiama jūsų kompiuteryje — tai ne naršyklės kortelė ir ne
  talpinama paslauga.
* **Pokalbiams ir failams veikia be interneto.** Pasirinkus vietinį modelį, jūsų pokalbiams ir failams nereikia
  interneto ryšio. Naujausia informacija, pavyzdžiui, orai ar kainos, gaunama iš viešųjų žiniatinklio paslaugų tik tada, kai įjungta „Interneto paieška“ (po naujo įdiegimo ji išjungta), ir niekada režimu „Tik lokaliai“.
* **Pagal numatytuosius nustatymus vietinis.** Pasirinkus vietinį modelį, užklausos ir failai apdorojami
  jūsų kompiuteryje.
* **Nepriklausomas nuo modelio.** Galima rinktis ir vietines vykdymo aplinkas, ir debesijos tiekėjus. Debesijos
  raktą pateikiate jūs; jis lieka jūsų kompiuteryje.
* **Patikrinamas.** Kiekvienas įrankio iškvietimas įrašomas su įvestimis ir rezultatais, todėl
  darbo eigą galima perskaityti vėliau.

{% hint style="warning" %}
VOOL yra beta versija Mac kompiuteriams su Apple Silicon, Mac kompiuteriams su Intel ir Linux x86_64. Mac programos
dar nėra notarizuotos Apple, todėl pirmą kartą atidarant macOS paprašys patvirtinimo;
[Diegimo](getting-started/install.md#pirmasis-atidarymas-mac-kompiuteryje) puslapyje parodyta, kaip tai padaryti. Prieš diegdami
perskaitykite [Leidimų būseną](trust/release-status.md).
{% endhint %}

## Du atsakymo būdai, be užuolankų

VOOL atsako dviem būdais, o pokalbio antraštė visada rodo, kuriuo būdu bus vykdoma kita
užklausa, — pavyzdžiui, **AUTOMATINIS** su **Pirmiausia vietoje**:

1. **Lokaliai** — darbas lieka šiame kompiuteryje.
2. **Debesys** — pasirinktas kontekstas siunčiamas tiesiai jūsų pasirinktam tiekėjui,
   iš jūsų kompiuterio, naudojant jūsų raktą.

Tarp jų nėra jokio Parad0x valdomo tarpininko. Išsamiau
rašoma skyriuje [Duomenų tvarkymas](trust/data-handling.md).

## Pagalba

* [DUK](help/faq.md) — dažniausiai užduodami klausimai
* [Problemų sprendimas](help/troubleshooting.md) — kai kas nors nepasileidžia
* [Discord](https://discord.gg/V9NkjP3Fzz) — paklauskite komandos

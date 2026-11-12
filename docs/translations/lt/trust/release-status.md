---
description: Kokiame etape yra VOOL, pasakyta tiesiai.
translation: machine
source_sha256: bf79dfc262c1aca9c489c8474ad2de5fa633b0b60a515f2835f52954c66ad73f
---

# Leidimų būsena

Šis puslapis yra prižiūrimas, lemiamas leidimų būsenos šaltinis. Kituose dokumentuose faktai apie leidimus
minimi tarp kitko; jei jie nesutampa su šiuo puslapiu, galioja šis puslapis.

## Dabartinis etapas

VOOL yra **beta** etape. Dabartinis leidimas yra
[**v0.7.7-beta**](https://github.com/Parad0x-Labs/vool/releases/tag/v0.7.7-beta), GitHub leidimas,
sukurtas iš `main` ties `8af687c`. Jame yra programos atsisiuntimai trims platformoms, kiekvienas su
paskelbta SHA-256 kontroline suma. Diegimas iš šaltinio kodo naudojant paleidimo scenarijų tebėra
palaikomas macOS, Linux ir Windows sistemose; žr. [Diegimas](../getting-started/install.md).

| Atsisiuntimas | Kam |
| --- | --- |
| `VOOL-0.7.7-beta-macos-arm64.dmg` | Mac kompiuteriams su Apple Silicon, macOS 14 ar naujesnė |
| `VOOL-0.7.7-beta-macos-x86_64.dmg` | Mac kompiuteriams su Intel, macOS 14 ar naujesnė |
| `vool-0.7.7-beta-linux-x86_64.tar.gz` | Linux x86_64, išbandyta su Ubuntu 22.04 ir 24.04 |

Šios repozitorijos leidimų įrašai apima GitHub kanalą. Kiti atsisiuntimo kanalai valdomi atskirai; bet
kokį atsisiuntimą patikrinkite pagal kontrolinę sumą, paskelbtą GitHub leidime.

| | Būsena |
| --- | --- |
| Vieši atsisiuntimai | macOS Apple Silicon DMG, macOS Intel DMG ir Linux x86_64 tar archyvas v0.7.7-beta GitHub leidime |
| Paskelbtos kontrolinės sumos | SHA-256 kiekvienam atsisiuntimui, leidime |
| Kodo pasirašymas | macOS programos pasirašytos ad hoc, ne Developer ID. Pasirašymas bus pridėtas po šios beta versijos, ir nuo jo šios beta versijos išleidimas nepriklauso |
| macOS notarizavimas | Nenotarizuota. macOS paprašo patvirtinti pirmąjį atidarymą (žr. toliau) |
| Windows | Šiame leidime nėra Windows atsisiuntimo; Windows sistemoje diegiama iš šaltinio kodo, ir šis palaikymas eksperimentinis |
| Atnaujinimai | Nuo 0.7.5 VOOL atsinaujina pats: įjungus „Prisijungti prie interneto savarankiškai“, jis parodo pranešimą ir vienu paspaudimu įdiegia naują versiją. Atnaujinant iš 0.7.4 ar 0.7.3 Mac kompiuteryje: įjunkite „Prisijungti prie interneto savarankiškai“ (Nustatymai > Privatumas ir leidimai) ir vieną kartą paspauskite „Tikrinti atnaujinimus“. Linux sistemoje arba iš 0.7.2, 0.7.1 ar 0.7.0: atsisiųskite 0.7.7 ir įdiekite ją ant dabartinės versijos. Jūsų pokalbiai ir nustatymai saugomi jūsų profilyje, už programos ribų. Žr. [Atnaujinimai ir migracija](../reference/updates-migration.md) |
| API stabilumas | Keičiasi tarp versijų |

## Ką tai reiškia jums

* Pirmą kartą paleidžiant Mac programą, macOS atsisako ją atidaryti, nes ji nėra notarizuota.
  Šiame etape tai tikėtina ir nereiškia, kad atsisiuntimas sugadintas. Patikrinkite kontrolinę sumą,
  tada atidarykite **System Settings → Privacy & Security** ir spustelėkite **Open Anyway**. Sistemoje
  macOS 14 galite vietoj to spustelėti programą laikydami nuspaudę Control ir pasirinkti **Open**.
* Pasirinkite Mac atsisiuntimą, atitinkantį jūsų procesorių. Apple Silicon programa neveikia Mac
  kompiuteryje su Intel ir tai praneša paleidžiant.
* Konfigūracijos formatai tarp versijų gali keistis.
* Kriptovaliutų funkcijos išjungtos, kol jų neįjungiate.

## Beta ir bandomojo etapo apribojimai, nurodyti aiškiai

| Galimybė | Būsena šioje versijoje |
| --- | --- |
| Kriptovaliutos ir piniginė | Pagal numatytuosius nustatymus išjungta. Kai jas įjungiate, VOOL naudoja mainnet; testiniai tinklai yra skiltyje Developer options. Kiekvienas mokėjimas eina per VOOL piniginę, su jos limitais, jūsų patvirtinimu ir kvitu. Žr. [Piniginė](../guides/wallet.md). |
| Pervedimai mainnet tinkle | Natyviosios monetos ir USDT Solana, Ethereum, BNB Smart Chain ir Arbitrum One tinkluose, siunčiami iš šiame įrenginyje sukurtos VOOL piniginės. USDC Solana ir Base tinkluose skirtas tik mokėjimų kanalams. Prijungta ar tik stebima paskyra gali gauti lėšas ir rodyti savo likutį, bet negali siųsti iš VOOL. |
| Mokėjimas per x402 | Svetainėms, kurios prašo x402 mokėjimo USDC tinkle Base arba Base Sepolia, galima sumokėti iš jūsų Crypto Pilot piniginės: vienu patvirtinimu PIN kodu, neviršijant jūsų limitų. Svetainės mokėjimų paslauga padengia tinklo mokestį. **Nustatymai → Kriptovaliutos → Payment channels** turi įjungimo / išjungimo jungiklį kiekvienam būdui, kuriuo piniginė gali sumokėti svetainei; išjungtas kanalas atmeta dar prieš ką nors pasirašant. Tokiu būdu dar nebuvo atliktas nė vienas tikras mokėjimas mainnet tinkle. Mokėjimo per Solana pay-kit atsisiuntimuose nėra; tas kanalas rodomas kaip nenaudojamas. |
| X | Iš bet kurio pokalbio: įrašų ir paskyrų, Spaces, tendencijų ir asmeninių žinučių (DM) paieška bei ankstesnių prognozių apie žetoną peržiūra. Viskas, kas veikia jūsų paskyroje, laukia jūsų sutikimo. |
| Diktavimas | Šioje beta versijoje laikinai nepasiekiamas; jis sugrįš su atnaujinimu. |
| Debesijos „burst“ režimas su klausimu | Klausimas prieš kiekvieną iškvietimą šioje versijoje dar neprijungtas. |
| Vietiniai modeliai | Kad vietinis modelis galėtų atsakyti, atidarykite **Nustatymai → Modeliai ir tiekėjai → Vietiniai modeliai** ir vieną kartą paspauskite jam **Certify**. |
| Mac kompiuteriai su Intel | Vietiniai modeliai veikia procesoriuje ir yra lėti. Sertifikavimas atsižvelgia į lėtesnius procesorius, bet ilgas atsakymas iš atminties gali viršyti laukimo laiką, todėl kol kas ten naudokite debesijos modelį. |
| Privatus pokalbis | Pasakykite „privatus pokalbis“, „neišsaugokite to“ arba „tik tarp mūsų“, ir niekas iš to pokalbio nebus išsaugota. Komandos veikia visomis 17 programos kalbų. |
| Pamiršimas | Pamiršimas išvalo VOOL atmintį ir atitinkamą vykdymo istoriją, todėl elementas nebėra prisimenamas ar rodomas sąraše, ir ištrina pamirštus žodžius iš VOOL vietinio atminties failo bei jo paieškos indekso. VOOL žodžius, kuriuos paprašėte pamiršti, laiko trumpame sąraše jūsų kompiuteryje, kad galėtų ir toliau juos atmesti. Kai ką nors pamiršite pokalbyje, VOOL tame pokalbyje to paties fakto atgal nebepriims; norėdami jį vėl pasakyti, pradėkite naują pokalbį. |
| Nuskenuoti PDF | Skaitomi naudojant įtaisytą Mac teksto atpažinimą, todėl tik Mac kompiuteriuose. |
| Darbalaukio augintinis | Augintinis gyvena VOOL lange. Ištempus jį už lango, jis neperkeliamas į darbalaukį. |
| Įgūdžių kodeksas | „Naršyti įgūdžių kodeksą“ rodo įgūdžius, išvardytus būtent jūsų VOOL versijai, o kiekvieną įgūdį peržiūrite prieš jį įdiegiant. „Paskelbti įgūdžių kodekse“ patikrina jūsų kompiuteryje sukurtą įgūdį, tada atidaro įgūdžių kodekso svetainę su užpildytais jo duomenimis; ten prisijungiate ir paspaudžiate Send, o pats VOOL nieko nesiunčia. Perduodamas tik įgūdžio tekstas, todėl įgūdžio su papildomais failais kol kas bendrinti negalima. |

Developer ID pasirašymas ir notarizavimas macOS sistemai bei pasirašytos kontrolinės sumos Linux sistemai
yra kiti leidimo kelio žingsniai; šiandien neteigiama, kad kuris nors iš jų jau yra.

---
description: Išlaidų ribos, kainos apsaugos ir kas nutinka, kai mokamas iškvietimas nutraukiamas.
translation: machine
source_sha256: a4a5246059942702330cbfc18e9872dc5b0f46192fa5b852bfc9060ca987eae6
---

# Išlaidų ribos ir kainos apsaugos

VOOL niekada neleidžia pinigų jūsų vardu be aiškios viršutinės ribos, kurią valdote jūs. Šiame puslapyje
aprašyti sluoksniai, saugantys mokamus modelių iškvietimus ir mokamus išteklius, ir kas nutinka, kai
mokamas iškvietimas nutraukiamas pusiaukelėje.

## Dvi kainos, viena taisyklė

* **Katalogo kaina** — kaina, kurią tiekėjas šiuo metu skelbia modeliui. VOOL ją aptinka; ji yra
  informacija, o ne leidimas.
* **Jūsų priimta kaina** — viršutinė riba, kurią patvirtinote tam tiekėjui ir modeliui Nustatymuose.
  Aptikta kaina, viršijanti jūsų ribą, atmetama **prieš** susisiekiant su tiekėju. Atmetime sakoma
  būtent tai: modelis nebuvo paleistas, niekas nebuvo išsiųsta, niekas nebuvo apmokestinta.

## Išlaidų limitai

Mokamiems iškvietimams VOOL prieš kiekvieną iškvietimą rezervuoja lėšas pagal limitus:

| Limitas | Reikšmė |
| --- | --- |
| Vienam iškvietimui | Numatomos vieno iškvietimo kainos viršutinė riba. |
| Vienai užduočiai | Didžiausia suma, kurią viena užduotis gali išleisti visiems savo iškvietimams. |
| Dienos / mėnesio | Viso laikotarpio mokamo naudojimo viršutinės ribos. |

Iškvietimas, kuris viršytų limitą, atmetamas prieš jį išsiunčiant. Atmetime įvardijamas limitas, dėl kurio
jis atmestas (`per_task_spend_cap_exceeded` ir giminingi kodai), o formuluotė sąmoningai kalba apie laiką
prieš siuntimą: *„running it would exceed your spend cap“* — niekada neteigiama, kad buvo susisiekta su tiekėju.

## Prisegtas modelis niekada tyliai nepakeičiamas

Jei aiškiai pasirenkate mokamą modelį ir apsauga jį atmeta, VOOL tyliai **neatsako** pigesniu modeliu.
Ėjimas baigiasi nesėkme, nurodant jūsų pasirinktą modelį ir tikrąją kliūtį, ir sakiniu
*„no other model was substituted.“* Patys pasirinkite kitą modelį arba sąmoningai padidinkite limitą.

## Apmokėta, bet atsakymas neatėjo

Jei už mokamą iškvietimą buvo nuskaičiuoti pinigai, bet atsakymas neatėjo, rezultatas yra **nežinomas** —
VOOL neapsimes, kad iškvietimas nepavyko ar pavyko:

* Vietoj jo neišgalvojamas joks atsakymas, ir nelaikoma, kad pinigai grąžinti.
* Mokėjimas lieka įrašuose kaip neišspręstas.
* Nustatymai jį rodo skiltyje **Paid calls waiting for an answer**, kur **Resume** dar kartą paprašo to
  paties atsakymo, **nemokant dar kartą**.

Todėl tokiais atvejais niekada nesiūloma aklai bandyti dar kartą: paprastas pakartotinis siuntimas galėtų
sumokėti du kartus. Resume yra vienintelis palaikomas kelias.

## Automatiniai mokėjimai iš piniginės (x402)

Svetainėms, kurios prašo x402 mokėjimo USDC tinkle Base arba Base Sepolia, galima sumokėti iš jūsų
Crypto Pilot piniginės, vienu patvirtinimu PIN kodu neviršijant šių limitų; tokiu būdu dar nebuvo atliktas
nė vienas tikras mokėjimas mainnet tinkle. Mokėjimo už iškvietimą parinktis modelių prekyvietėje
Nustatymuose moka iš Solana Mainnet piniginės, vienu patvirtinimu kiekvienam iškvietimui neviršijant šių
limitų, ir dar nebuvo išbandyta tikroje grandinėje.
Iš piniginės apmokami ištekliai (žr. [Piniginė](../guides/wallet.md)) turi savo automatinių mokėjimų limitą.
Išteklius, prašantis daugiau nei šis limitas, tiesiog neapmokamas; galite sąmoningai padidinti limitą arba
sumokėti už išteklių per aiškų pasiūlymą su jūsų patvirtinimu.

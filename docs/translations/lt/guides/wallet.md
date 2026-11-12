---
description: Neprivaloma Crypto Pilot piniginė — tinklai, x402 mokėjimai, DNA mokesčiai ir atkūrimas, tokie, kokius ši versija iš tikrųjų pateikia.
translation: machine
source_sha256: 46121a5ed26be175ddd3c66b269df356e074adbb57c8e9806461b80197613cc2
---

# Piniginė

Piniginė yra **neprivaloma ir pagal numatytuosius nustatymus išjungta**. Ji skirta tam, kad VOOL galėtų
laikyti nedidelę kriptovaliutos sumą DI paslaugoms, kurios ima mokestį už kiekvieną užklausą. Norint naudoti
VOOL, nieko, kas su ja susiję, nereikia.

Šiame puslapyje aprašyta, ką dabartinė versija iš tikrųjų palaiko. Piniginė veikia **bandomuoju** režimu:
galimybės pridedamos po vieną maršrutą, o prie kiekvieno toliau aprašyto maršruto tiksliai nurodyta, ką jis
daro.

## Tinklo aplinkos

Kai kriptovaliutų funkcijos įjungtos, VOOL veikia **Mainnet** aplinkoje: Solana, Robinhood Chain, Base,
Ethereum, BNB ir Arbitrum One.
Įprasta sąranka niekada neprašo pasirinkti tinklo aplinkos.

* **Mainnet** — numatytoji kiekvieno įdiegimo aplinka, įskaitant įdiegimus, atnaujintus su senesnėmis
  testinių tinklų paskyromis. Kiekvienas išlaidų limitas, patvirtinimas ir kvitas taikomas taip, kaip
  aprašyta toliau.
* **Test networks** — Solana Devnet, Robinhood Chain Testnet ir kiti testiniai maršrutai. Tik kūrėjams:
  perjungiama per **Nustatymai → Kriptovaliutos → Developer options**.

Paskyros, likučiai ir kvitai priklauso vienai aplinkai ir niekada nepereina iš vienos į kitą; kitos
aplinkos paskyros lieka rodomos savo tinkle. Užklausa tinklui iš kitos aplinkos atmetama, nieko nesukuriant,
nesiūlant ir nepasirašant.

{% hint style="warning" %}
Ankstesnėje dokumentacijoje buvo rašoma, kad šioje versijoje mainnet neįmanomas. Tai buvo tiesa iki
bandomojo etapo, bet nebėra: mainnet eilutės dabar yra. Tiesa lieka siauresnis teiginys — toliau esančiuose
skyriuose apie pervedimus ir mokėjimus tiksliai nurodyta, kurie maršrutai yra mainnet tinkle.
{% endhint %}

## Kas kokiu maršrutu juda

* **Pervedimais perkeliamos natyviosios monetos** (pavyzdžiui, SOL Solana tinkle, ETH Base, Ethereum ar
  Arbitrum One tinkluose) ir **USDT Solana, Ethereum, BNB Smart Chain ir Arbitrum One tinkluose**.
  Grandinė yra kiekvieno išsaugoto kontakto adreso ir kiekvieno patvirtinimo dalis. Žetonų pervedimams
  taip pat reikia tos grandinės natyviosios monetos mokesčiams; kainos pasiūlymas rodo abi sumas,
  neviršijant jūsų limitų.
* **USDC Solana ir Base tinkluose prieinamas tik mokėjimų kanalams.** Jis nesiūlomas įprastiems
  siuntimams. Pagrindiniame piniginės likučio rodinyje rodomos natyviosios monetos, o ne visas žetonų
  portfelis.
* Kiekvienas pervedimas pirmiausia parodomas peržiūrai (**kainos pasiūlymas**: suma, mokesčio viršutinė
  riba, gavėjas, tinklas) ir reikalauja jūsų aiškaus patvirtinimo, susieto būtent su ta peržiūra. Pakeiskite
  ką nors, ir patvirtinimas nebegalioja — niekas nepasirašoma.
* Jei vardas atitinka kelis kontaktus ar piniginių adresus, jūsų paprašoma pasirinkti. Patikrinkite tikslų
  adresą ir grandinę. Pakeistas adresas ar pervadintas kontaktas panaikina ankstesnę peržiūrą.
* Mainnet tinkle siųsti gali tik šiame įrenginyje sukurta VOOL piniginė. Prijungta išorinė piniginė ar tik
  stebimas adresas gali gauti lėšas ir rodyti savo likutį; siuntimas ar mokėjimas mokėjimų kanalu iš jų
  atmetamas dar prieš paprašant bet kokio patvirtinimo, ir niekas nepasirašoma.
* Veikla įrašo per VOOL atliktus mokėjimus ir jų kvitus. Tai nėra visa gaunamų mokėjimų istorija. Ši
  piniginė nesiūlo swap ar bridge operacijų.

## Kaip patvirtinamas mokėjimas

1. VOOL parengia pasiūlymą su tiksliomis sumomis ir mokesčio viršutine riba.
2. Jūs jį patvirtinate savo PIN kodu ar slaptažodžiu (arba buvimo prie įrenginio patvirtinimu, kur tai
   įmanoma).
3. Mokėjimas pasirašomas ir išsiunčiamas, o kvitas įrašomas.

Patvirtinimai nepereina iš vieno pasiūlymo į kitą. Neteisingas PIN kodas nieko nepasirašo. Per daug
neteisingų bandymų kuriam laikui užrakina piniginę — sąmoningai, nurodant laukimo laiką.

## x402 automatiniai mokėjimai

x402 yra mokėjimo už kiekvieną užklausą protokolas, kurį naudoja kai kurios svetainės ir DI paslaugos.
**Svetainėms, kurios prašo x402 mokėjimo USDC tinkle Base** (arba Base Sepolia, skiltyje Developer
options), galima sumokėti iš jūsų Crypto Pilot piniginės: vienu patvirtinimu PIN kodu, neviršijant jūsų
limitų. Svetainės mokėjimų paslauga padengia tinklo mokestį. **Tokiu būdu dar nebuvo atliktas nė vienas
tikras mokėjimas mainnet tinkle.** Kiti testiniai tinklai (Solana Devnet, Ethereum Sepolia ir BNB testnet)
lieka skiltyje Developer options. **Nustatymai → Kriptovaliutos → Payment channels** išvardija kiekvieną
būdą, kuriuo piniginė gali sumokėti svetainei, kiekvieną su savo įjungimo / išjungimo jungikliu; išjungtas
kanalas atmeta dar prieš ką nors pasirašant. Mokėjimo per Solana pay-kit atsisiuntimuose nėra, todėl tas
kanalas rodomas kaip nenaudojamas. **Modelių prekyvietė:** mokėjimo už iškvietimą parinktis modelių prekyvietėje Nustatymuose gali mokėti iš Solana Mainnet
Crypto Pilot piniginės USDC arba SOL. Kiekvienas iškvietimas laukia jūsų patvirtinimo, neviršijant jūsų
piniginės limitų, ir rodo 0,1 % VOOL mokestį. Šis kelias dar nebuvo išbandytas tikroje grandinėje.
Kai mokamas išteklius naudoja x402:

* Mokėjimai eina per tuos pačius piniginės limitus ir patvirtinimo eigą.
* Automatiniams mokėjimams taikomas limitas vienam mokėjimui; išteklius, viršijantis limitą, **neapmokamas**
  (galite sąmoningai padidinti limitą arba sumokėti per aiškų pasiūlymą).
* Jei mokėjimo nepavyko užbaigti, rezultatas nurodomas kaip **nežinomas** — prieš bandydami dar kartą,
  patikrinkite jo būseną; VOOL neskatins aklai sumokėti antrą kartą.

## DNA paslaugos mokestis

Modelių prekyvietės mokėjimo už iškvietimą x402 maršrutas ima **0,1 % (10 bazinių punktų)** paslaugos mokestį nuo kiekvieno priimto
mokėjimo tiekėjui, rodomą atskirai nuo mokėjimo ir atskirai nuo tinklo mokesčio. Niekas kitas jo
nesukaupia: įprastiems siuntimams, piniginės sukūrimui, vietiniam išvedimui ir iškvietimams su jūsų pačių
API raktu (BYOK) netaikomas **joks** paslaugos mokestis. Sukaupti mokesčiai surenkami ekonomiškai (su
vėlesniu natyviuoju mokėjimu, vienu patvirtinimu, tik kai surinkimo kaina ribota) — niekada ne netikėtu
atskiru nuskaičiavimu.

## Atkūrimas

* **Atkūrimo frazė parodoma tiksliai vieną kartą**, kuriant piniginę.
* Bandomosios piniginės atsarginis raktas taip pat atskleidžiamas vieną kartą sąrankos metu, ir vėliau jo
  eksportuoti negalima. Jei jo neišsaugojote, atšaukite sąranką ir nesiųskite lėšų tuo adresu.
* Senesnė VOOL piniginė, sukurta iki Crypto Pilot, gali vieną kartą eksportuoti savo privatųjį raktą, kad
  jį būtų galima importuoti į kitą piniginės programą. Eksportas atsidaro tik per jūsų Mac prisijungimą
  (Touch ID arba Mac slaptažodį), niekada ne piniginės PIN kodu, o raktas parodomas vieną kartą ir
  neišsaugomas.
* Praradote piniginę? Atkurkite ją iš frazės, kurią užsirašėte kurdami piniginę. Atkūrimo įrodymas, kuris
  neatitinka būtent šios piniginės, atmetamas prieš bet kokį įrašymą.

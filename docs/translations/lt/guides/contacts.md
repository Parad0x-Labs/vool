---
description: Kontaktai — išsaugoti žmonės ir galiniai taškai su pakeitimų peržiūra, kuri apsaugo kiekvieną redagavimą.
translation: machine
source_sha256: 8be5d771229a4d6c4b439af014f0407649ed694fd26f9fd880009d25f5b8a347
---

# Kontaktai

Kontaktų sąrašas yra vietinė, struktūruota adresų knyga (žmonės, jų galiniai taškai ir mokėjimų
slapyvardžiai), kurią VOOL gali naudoti, užuot prašęs jūsų iš naujo įvesti adresą. Kadangi adresų knyga
naudojama mokėjimams ir žinutėms, jos redagavimai yra **apsaugoti**: peržiūrimi, patvirtinami ir įrašomi.

## Apsaugoti redagavimai

Išsaugoto kontakto keitimas vyksta dviem žingsniais:

1. **Peržiūra** — pakeitimas parengiamas ir parodomas. Jei įrašas pasikeitė nuo tada, kai jį atidarėte
   (kitame lange, kitame įrenginyje), pakeitimas atmetamas su pranešimu *„changed after this was
   reviewed; review it again. Nothing changed.“*
2. **Patvirtinimas** — pakeitimas įrašomas tik su jūsų PIN kodu arba slaptažodžiu peržiūros ekrane.

Kiekviename atmetime aiškiai pasakoma, kad **niekas nebuvo išsaugota ar pakeista** — nes niekas ir nebuvo.
Jei jūsų PIN kodas buvo priimtas, bet pats išsaugojimas nepavyko, pranešime sakoma būtent tai: PIN kodas
priimtas, išsaugokite dar kartą, kol patvirtinimas dar galioja.

## Apsaugos

* Vienu metu vienas pakeitimas vienam kontaktui; laukiančių pakeitimų skaičius ribotas.
* Kontakto ištrynimas tyliai neperrašo istorijos: nuorodos į ištrintą kontaktą nurodomos kaip
  *„That contact was deleted“* — niekada tyliai nepriskiriamos iš naujo.
* Importai niekada netaikomi iš dalies: jei šaltinio nepavyko perskaityti, niekas nebuvo importuota.

## Kodėl taip sukurta

Klaida mokėjimo adrese reiškia neatgaunamus pinigus. Peržiūros žingsnis yra tam, kad joks modelio ėjimas,
joks masinis redagavimas ir joks pasenęs langas negalėtų pakeisti, į ką nurodo „Alice“, be žmogaus,
patvirtinančio tikslų galutinį įrašą.

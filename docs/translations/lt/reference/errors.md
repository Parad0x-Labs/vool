---
description: Kaip perskaityti VOOL klaidą, ar kas nors jau įvyko ir kur paaiškintas kiekvienas kodas.
translation: machine
source_sha256: ccb01d4c778254a055fd67f46241c670aac90cfc83f168b923a19cef1f949de2
---

# Klaidos ir klaidų kodai

Kai kas nors nepavyksta, VOOL paprastais žodžiais pasako, kas nutiko, kas galbūt jau įvyko
ir ką daryti toliau. Kiekviena nesėkmė taip pat turi pastovų kodą. Jį rasite techninėje informacijoje
po klaida, komandų eilutėje (`wallet_export_refused: …`) ir API atsakymuose. Šiame puslapyje
paaiškinta, kaip perskaityti klaidą. [Klaidų knygoje](../ERROR_BOOK.md) paaiškintas kiekvienas kodas.

## Kaip perskaityti klaidą

Prieš bandydami dar kartą, patikrinkite tris dalykus:

1. **Kas nutiko.** Pranešimas. Jame niekada nėra vidinių kelių ar slaptų duomenų.
2. **Kas galbūt jau įvyko.** Dauguma atmetimų sustoja dar prieš ką nors išsiunčiant ar pakeičiant,
   ir pranešime tai pasakoma. Kai kurių nesėkmių atveju neįmanoma žinoti, ar veiksmas baigėsi, pavyzdžiui,
   kai laukimo laikas baigiasi jau išsiuntus užklausą. Tada pranešime sakoma, kad rezultatas nežinomas, ir
   prašoma patikrinti prieš bandant dar kartą. Mokėjimas niekada netyčia neišsiunčiamas du kartus: antras
   to paties mokėjimo siuntimas atmetamas su kodu
   [`wallet_duplicate_payment`](../ERROR_BOOK.md#wallet-duplicate-payment).
3. **Ką daryti toliau.** Vienas konkretus žingsnis.

Reikšmę nusako kodas. HTTP būsena, pavyzdžiui, 403 ar 429, tik parodo, kaip užklausai sekėsi pakeliui,
o du skirtingi atmetimai gali turėti tą pačią būseną. Skaitykite kodą, o ne būseną.

## Kodai ir Klaidų knyga

Kodas niekada nepervadinamas ir niekada nenaudojamas kita reikšme.
[Klaidų knyga](../ERROR_BOOK.md) generuojama iš pačios programos klaidų katalogo, todėl joje pateikti
kodai, pranešimai ir tolesni žingsniai yra lygiai tokie, kokius rodo programa. Kiekvienas kodas turi savo
skyrių: ieškokite puslapyje kodo tiksliai tokio, kokį gavote, arba nuorodą į skyrių sudarykite iš knygos
adreso, `#` ir kodo, kuriame apatiniai brūkšniai pakeisti brūkšneliais, pavyzdžiui,
[`wallet_broadcast_failed`](../ERROR_BOOK.md#wallet-broadcast-failed).

Dažniausiai pasitaikantys kodai:

| Kodas | Pavadinimas | Kas galbūt jau įvyko |
| --- | --- | --- |
| [`permission_denied`](../ERROR_BOOK.md#permission-denied) | Leidimas nesuteiktas | Niekas nepakeista. |
| [`provider_unavailable`](../ERROR_BOOK.md#provider-unavailable) | Tiekėjas nepasiekiamas | Niekas neišsiųsta. |
| [`provider_exhausted`](../ERROR_BOOK.md#provider-exhausted) | Pasiektas tiekėjo limitas | Užklausa pasiekė tiekėją. |
| [`timeout`](../ERROR_BOOK.md#timeout) | Baigėsi laukimo laikas | Nežinoma: prieš bandydami dar kartą, patikrinkite. |
| [`cancelled`](../ERROR_BOOK.md#cancelled) | Atšaukta | Nežinoma: dalis galbūt jau įvykdyta. |
| [`credential_failure`](../ERROR_BOOK.md#credential-failure) | Įrašyto rakto nepavyko perskaityti | Niekas neišsiųsta ir nepakeista. |
| [`unknown`](../ERROR_BOOK.md#unknown) | Netikėta klaida | Nežinoma: prieš bandydami dar kartą, patikrinkite. |
| [`wallet_quote_mismatch`](../ERROR_BOOK.md#wallet-quote-mismatch) | Patvirtinimas nebeatitinka peržiūros | Niekas nepasirašyta ir neišsiųsta. |
| [`wallet_broadcast_failed`](../ERROR_BOOK.md#wallet-broadcast-failed) | Mokėjimo paskelbti nepavyko | Nežinoma: prieš bandydami dar kartą, patikrinkite mokėjimo būseną. |
| [`wallet_duplicate_payment`](../ERROR_BOOK.md#wallet-duplicate-payment) | Mokėjimas jau pasiūlytas ar išsiųstas | Nieko naujo neišsiųsta; ankstesnis mokėjimas užfiksuotas įrašuose. |

## Klaidos ir kiti rezultatai

VOOL juos skiria, kaip ir jo formuluotės:

* **Klaida.** Kažkas nepavyko. Kodas ir pranešimas nurodo, kas, ir ką daryti.
* **Laukiama jūsų.** Atidaryta leidimo užklausa arba mokėjimo patvirtinimas. Niekas nebuvo vykdoma
  ir nebus vykdoma, kol neatsakysite. Žr. [Leidimai](../concepts/permissions.md).
* **Sustabdyta dėl išlaidų limito ar kainos apsaugos.** Atmetama dar prieš susisiekiant su tiekėju,
  o pranešime niekada neužsimenama, kad tiekėjo buvo paklausta. Žr.
  [Išlaidų ribos ir kainos apsaugos](../guides/spending-limits.md).
* **Atšaukta.** Tai buvo sustabdyta dar nepasibaigus — jūsų arba VOOL. Dalis galbūt jau įvykdyta,
  todėl prieš paleisdami iš naujo patikrinkite, kas pasikeitė.
* **Čia neįmanoma.** Ši versija ar šis kompiuteris to padaryti negali, o pranešime nurodoma, kas gali:
  mokėjimo pasiūlymas, kurio schemos VOOL negali patikrinti nuo pradžios iki galo, arba neprivalomas
  priedas, kuris neįdiegtas.
* **Rezultatas nežinomas.** Veiksmas galėjo baigtis, o galėjo ir nesibaigti, pavyzdžiui, mokėjimas, kurio
  paskelbimas taip ir nebuvo patvirtintas. Pirmiausia patikrinkite jo būseną. VOOL niekada aklai nemoka dar
  kartą.

## Klaidos su savais kodais

Kelios VOOL dalys savo nesėkmes paaiškina savais trumpais kodais tame ekrane, kuriame nesėkmė įvyksta,
ir šių kodų nėra Klaidų knygoje: priedai (pavyzdžiui, per didelis failas arba įklijuotame faile rasti
prisijungimo duomenys), programos atnaujinimai, projektų aplankai, modelio pasirinkimas, išlaidų limitai,
kontaktai, papildiniai ir įgūdžiai, balso įvestis, saugomi duomenys ir klaidų ataskaitos. Jų pranešimai
taip pat pasako, kas nutiko ir ką daryti. Žr.
[Pokalbiai ir priedai](../concepts/chats-and-attachments.md),
[Atnaujinimai ir migracija](updates-migration.md) ir
[Pranešimai apie klaidas ir privatumas](../trust/bug-reporting.md).

## Kodas, kurio neatpažįstate

Kodas, kurio nėra Klaidų knygoje, nėra iš šios VOOL versijos. Patikrinkite, kuri versija jį parodė, tada
ieškokite tos versijos [Klaidų knygoje](../ERROR_BOOK.md). Jei jis vis dėlto iš šios versijos, praneškite
apie jį, nurodydami kodą tiksliai taip, kaip jis parašytas, ir nuorodą (`fault-` ir dvylika simbolių),
jei atsakyme ji buvo. Nesėkmė, kurios VOOL negali klasifikuoti, rodoma kaip
[`unknown`](../ERROR_BOOK.md#unknown), niekada kaip spėjama priežastis.
Žr. [Pranešimai apie klaidas ir privatumas](../trust/bug-reporting.md).

## Konkrečių simptomų sprendimas

Diegimo, modelių ir platformų problemos aprašytos skyriuje [Problemų sprendimas](../help/troubleshooting.md),
o pokalbių, atminties ir piniginės susigrąžinimas — skyriuje [Atsarginės kopijos ir atkūrimas](../help/backup-and-recovery.md).

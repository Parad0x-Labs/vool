---
translation: machine
source_sha256: 5a872f9502c66828af4dfb1e7e687226facc2aaad8cb6544054c90e1fafe8d7d
---

# VOOL klaidų knyga

<!-- GENERATED FILE — do not hand-edit. Regenerate with `python -m tools.generate_error_book`. -->

<!-- GENERATED FILE: the app's own texts for each code, and the words in tools/docs/error_book_words.json. Regenerate with tools/docs/translate_error_book.py, never by hand. -->

Visos klaidos, kurias VOOL gali jums parodyti, sugeneruotos iš pačios programos klaidų katalogo: toliau pateikti kodai, pranešimai ir tolesni žingsniai yra būtent tai, ką rodo programa.

- **Schema:** `vool.fault.v1`
- **Katalogo versija:** `1`
- **Katalogo kontrolinė suma:** `2d2c27206b99b26ca524806ec42ac41eac2f0d28a110a74f6ac5ef42006f313d`
- **Gedžių skaičius:** 59

Kiekvienas kodas yra pastovus ir niekada nenaudojamas iš naujo kita reikšme. Kiekvienas kodas turi savo skyrių toliau, kurio antraštė yra pats kodas, todėl ieškodami šiame puslapyje kodo taip, kaip jį gavote, rasite jo skyrių, o nuoroda į skyrių yra šio puslapio adresas, po kurio eina `#` ir kodas, kuriame apatiniai brūkšniai pakeisti brūkšneliais. Kiekviename skyriuje nurodyta, kas nutiko, kas galbūt jau įvyko, ką daryti toliau ir ar gali padėti bandymas dar kartą. Jei jūsų gauto kodo šioje knygoje nėra, jis ne iš šios VOOL versijos: patikrinkite, kuri versija jį parodė, arba praneškite apie jį, nurodydami kodą taip, kaip jis parašytas.

## Saugumas

| Kodas | Pavadinimas | Rimtumas | Kas galbūt jau įvyko |
|---|---|---|---|
| [`confinement_refusal`](#confinement-refusal) | Pakeitimas už leistinos apimties ribų | didelis | Niekas nebuvo išsiųsta, pasirašyta ar pakeista |
| [`credential_failure`](#credential-failure) | Įrašyto rakto nepavyko perskaityti | didelis | Niekas nebuvo išsiųsta, pasirašyta ar pakeista |
| [`wallet_backup_unavailable`](#wallet-backup-unavailable) | Atsarginė kopija jau parodyta vieną kartą | didelis | Niekas nebuvo išsiųsta, pasirašyta ar pakeista |
| [`wallet_caller_refused`](#wallet-caller-refused) | Pradėta ne iš paties VOOL lango | didelis | Niekas nebuvo išsiųsta, pasirašyta ar pakeista |
| [`wallet_card_data_refused`](#wallet-card-data-refused) | Kortelės duomenys atmesti | didelis | Niekas nebuvo išsiųsta, pasirašyta ar pakeista |
| [`wallet_chain_identity_mismatch`](#wallet-chain-identity-mismatch) | Tinklo galutinio taško tapatybė neįrodyta | didelis | Užklausa pasiekė išorinę paslaugą; joks mokėjimas nebuvo atliktas |
| [`wallet_environment_inactive`](#wallet-environment-inactive) | Netinkama tinklo aplinka | vidutinis | Niekas nebuvo išsiųsta, pasirašyta ar pakeista |
| [`wallet_export_pin_not_accepted`](#wallet-export-pin-not-accepted) | Eksportas atrakinamas tik įrenginio autentifikavimu | didelis | Niekas nebuvo išsiųsta, pasirašyta ar pakeista |
| [`wallet_export_refused`](#wallet-export-refused) | Senasis eksportavimo būdas nebenaudojamas | didelis | Niekas nebuvo išsiųsta, pasirašyta ar pakeista |
| [`wallet_export_unavailable`](#wallet-export-unavailable) | Eksportas šiai piniginei neprieinamas | didelis | Niekas nebuvo išsiųsta, pasirašyta ar pakeista |
| [`wallet_legacy_surface_retired`](#wallet-legacy-surface-retired) | Senasis pinigų kelias nebenaudojamas | didelis | Niekas nebuvo išsiųsta, pasirašyta ar pakeista |
| [`wallet_network_disabled`](#wallet-network-disabled) | Tinklas nedeklaruotas šiai piniginei | didelis | Niekas nebuvo išsiųsta, pasirašyta ar pakeista |
| [`wallet_outbound_refused`](#wallet-outbound-refused) | Užklausa už patvirtintų galutinių taškų ribų | didelis | Niekas nebuvo išsiųsta, pasirašyta ar pakeista |
| [`wallet_quote_mismatch`](#wallet-quote-mismatch) | Patvirtinimas nebeatitinka peržiūros | didelis | Niekas nebuvo išsiųsta, pasirašyta ar pakeista |
| [`wallet_recovery_refused`](#wallet-recovery-refused) | Atkūrimo įrodymas netinka šiai piniginei | didelis | Niekas nebuvo išsiųsta, pasirašyta ar pakeista |
| [`wallet_signature_invalid`](#wallet-signature-invalid) | Parašas neatitiko piniginės rakto | didelis | Užklausa pasiekė išorinę paslaugą; joks mokėjimas nebuvo atliktas |
| [`wallet_unlock_throttled`](#wallet-unlock-throttled) | Piniginė užrakinta po nepavykusių atrakinimų | didelis | Niekas nebuvo išsiųsta, pasirašyta ar pakeista |

## Vientisumas

| Kodas | Pavadinimas | Rimtumas | Kas galbūt jau įvyko |
|---|---|---|---|
| [`evidence_corruption`](#evidence-corruption) | Atsakymo įrašai nepraėjo vientisumo patikros | didelis | Įvyko vietinis įrašas ar pakeitimas |
| [`integrity_verification_failure`](#integrity-verification-failure) | Įrašas nepraėjo apsaugos nuo klastojimo patikros | kritinis | Įvyko vietinis įrašas ar pakeitimas |
| [`unsupported_claim`](#unsupported-claim) | Nepagrįsti teiginiai sulaikyti | vidutinis | Įvyko vietinis įrašas ar pakeitimas |
| [`wallet_duplicate_payment`](#wallet-duplicate-payment) | Mokėjimas jau pasiūlytas ar išsiųstas | vidutinis | Įvyko vietinis įrašas ar pakeitimas |

## Politika

| Kodas | Pavadinimas | Rimtumas | Kas galbūt jau įvyko |
|---|---|---|---|
| [`evm_pocket_custody_unavailable`](#evm-pocket-custody-unavailable) | Senoji kišenė skirta tik Solanai | mažas | Niekas nebuvo išsiųsta, pasirašyta ar pakeista |
| [`permission_denied`](#permission-denied) | Leidimas nesuteiktas | vidutinis | Niekas nebuvo išsiųsta, pasirašyta ar pakeista |
| [`wallet_acknowledgement_required`](#wallet-acknowledgement-required) | Reikia patvirtinimo | mažas | Niekas nebuvo išsiųsta, pasirašyta ar pakeista |
| [`wallet_amount_invalid`](#wallet-amount-invalid) | Suma netiksli šiai monetai | mažas | Niekas nebuvo išsiųsta, pasirašyta ar pakeista |
| [`wallet_approval_rejected`](#wallet-approval-rejected) | Patvirtinimas nepriimtas | vidutinis | Niekas nebuvo išsiųsta, pasirašyta ar pakeista |
| [`wallet_confirmation_required`](#wallet-confirmation-required) | Reikia įrašyto patvirtinimo | mažas | Niekas nebuvo išsiųsta, pasirašyta ar pakeista |
| [`wallet_credential_mismatch`](#wallet-credential-mismatch) | Įrašai nesutapo | mažas | Niekas nebuvo išsiųsta, pasirašyta ar pakeista |
| [`wallet_dependency_unavailable`](#wallet-dependency-unavailable) | EVM bibliotekos neįdiegtos | vidutinis | Niekas nebuvo išsiųsta, pasirašyta ar pakeista |
| [`wallet_device_auth_denied`](#wallet-device-auth-denied) | Atrakinimas įrenginiu atmestas | mažas | Niekas nebuvo išsiųsta, pasirašyta ar pakeista |
| [`wallet_disabled`](#wallet-disabled) | Piniginė išjungta | mažas | Niekas nebuvo išsiųsta, pasirašyta ar pakeista |
| [`wallet_insufficient_funds`](#wallet-insufficient-funds) | Balansas nepadengia sumos ir komisinių | mažas | Užklausa pasiekė išorinę paslaugą; joks mokėjimas nebuvo atliktas |
| [`wallet_limit_exceeded`](#wallet-limit-exceeded) | Viršytas išlaidų limitas | vidutinis | Niekas nebuvo išsiųsta, pasirašyta ar pakeista |
| [`wallet_not_found`](#wallet-not-found) | Piniginė, pasiūlymas ar gavėjas neatpažintas | mažas | Niekas nebuvo išsiųsta, pasirašyta ar pakeista |
| [`wallet_password_invalid`](#wallet-password-invalid) | Netinkamas piniginės slaptažodis | mažas | Niekas nebuvo išsiųsta, pasirašyta ar pakeista |
| [`wallet_paykit_unavailable`](#wallet-paykit-unavailable) | Solana papildinys pay neįdiegtas | vidutinis | Niekas nebuvo išsiųsta, pasirašyta ar pakeista |
| [`wallet_payment_channel_off`](#wallet-payment-channel-off) | Mokėjimo kanalas išjungtas | mažas | Niekas nebuvo išsiųsta, pasirašyta ar pakeista |
| [`wallet_pin_invalid`](#wallet-pin-invalid) | Netinkamas PIN | mažas | Niekas nebuvo išsiųsta, pasirašyta ar pakeista |
| [`wallet_quote_expired`](#wallet-quote-expired) | Numatymas baigė galioti arba buvo pakeistas | mažas | Niekas nebuvo išsiųsta, pasirašyta ar pakeista |
| [`wallet_recipient_refused`](#wallet-recipient-refused) | Gavėjas negali to saugiai priimti | vidutinis | Niekas nebuvo išsiųsta, pasirašyta ar pakeista |
| [`wallet_request_ambiguous`](#wallet-request-ambiguous) | Tinka daugiau nei viena paskyra ar tinklas | mažas | Niekas nebuvo išsiųsta, pasirašyta ar pakeista |
| [`wallet_send_needs_vool_wallet`](#wallet-send-needs-vool-wallet) | Siuntimui reikia VOOL Wallet | mažas | Niekas nebuvo išsiųsta, pasirašyta ar pakeista |
| [`wallet_setup_state_invalid`](#wallet-setup-state-invalid) | Sąrankos žingsnis dabar netaikomas | mažas | Niekas nebuvo išsiųsta, pasirašyta ar pakeista |
| [`wallet_signing_unavailable`](#wallet-signing-unavailable) | Piniginė negali iš čia pasirašyti | vidutinis | Niekas nebuvo išsiųsta, pasirašyta ar pakeista |
| [`wallet_storage_class_refused`](#wallet-storage-class-refused) | Raktų saugojimas tik atmintyje | vidutinis | Niekas nebuvo išsiųsta, pasirašyta ar pakeista |
| [`wallet_x402_cap_exceeded`](#wallet-x402-cap-exceeded) | Apmokamas išteklius virš automatinės ribos | vidutinis | Niekas nebuvo išsiųsta, pasirašyta ar pakeista |
| [`wallet_x402_sdk_unavailable`](#wallet-x402-sdk-unavailable) | Papildinys x402 neįdiegtas | vidutinis | Niekas nebuvo išsiųsta, pasirašyta ar pakeista |
| [`x402_scheme_unavailable`](#x402-scheme-unavailable) | Mokėjimo schema nepalaikoma | mažas | Niekas nebuvo išsiųsta, pasirašyta ar pakeista |

## Prieinamumas

| Kodas | Pavadinimas | Rimtumas | Kas galbūt jau įvyko |
|---|---|---|---|
| [`provider_exhausted`](#provider-exhausted) | Pasiektas tiekėjo limitas | vidutinis | Užklausa pasiekė išorinę paslaugą; joks mokėjimas nebuvo atliktas |
| [`provider_unavailable`](#provider-unavailable) | Tiekėjas nepasiekiamas | vidutinis | Niekas nebuvo išsiųsta, pasirašyta ar pakeista |
| [`timeout`](#timeout) | Baigėsi laukimo laikas | vidutinis | Rezultatas nežinomas — prieš bandydami dar kartą patikrinkite būseną |
| [`tool_unavailable`](#tool-unavailable) | Įrankis neprieinamas | mažas | Niekas nebuvo išsiųsta, pasirašyta ar pakeista |
| [`wallet_broadcast_failed`](#wallet-broadcast-failed) | Mokėjimo paskelbti nepavyko | vidutinis | Rezultatas nežinomas — prieš bandydami dar kartą patikrinkite būseną |
| [`wallet_device_auth_unavailable`](#wallet-device-auth-unavailable) | Atrakinimas įrenginiu nepasiekiamas | vidutinis | Niekas nebuvo išsiųsta, pasirašyta ar pakeista |
| [`wallet_quote_unavailable`](#wallet-quote-unavailable) | Balansas ar komisiniai nežinomi | vidutinis | Užklausa pasiekė išorinę paslaugą; joks mokėjimas nebuvo atliktas |
| [`wallet_security_key_needed`](#wallet-security-key-needed) | Reikia saugos rakto | mažas | Niekas nebuvo išsiųsta, pasirašyta ar pakeista |
| [`wallet_simulation_failed`](#wallet-simulation-failed) | Mokėjimas nepraėjo modeliavimo | vidutinis | Užklausa pasiekė išorinę paslaugą; joks mokėjimas nebuvo atliktas |

## Atšaukimas

| Kodas | Pavadinimas | Rimtumas | Kas galbūt jau įvyko |
|---|---|---|---|
| [`cancelled`](#cancelled) | Atšaukta | informacinis | Rezultatas nežinomas — prieš bandydami dar kartą patikrinkite būseną |

## Vidinės

| Kodas | Pavadinimas | Rimtumas | Kas galbūt jau įvyko |
|---|---|---|---|
| [`unknown`](#unknown) | Netikėta klaida | vidutinis | Rezultatas nežinomas — prieš bandydami dar kartą patikrinkite būseną |

## Žinynas pagal kodą

Visi kodai abėcėlės tvarka. Skyriaus antraštė yra pastovus kodo inkaras.

<a name="cancelled"></a>

### cancelled

**Atšaukta**

- **Kas nutiko:** Tai buvo atšaukta, todėl nebuvo baigta.
- **Kas galbūt jau įvyko:** Rezultatas nežinomas — prieš bandydami dar kartą patikrinkite būseną
- **Ką daryti toliau:** Veiksmo nereikia; jei norite, kad būtų atlikta, pradėkite iš naujo.
- **Bandymas dar kartą:** Nesiųskite tos pačios užklausos dar kartą nepakeistos.
- **Rimtumas:** informacinis · **Kategorija:** atšaukimas

<a name="confinement-refusal"></a>

### confinement_refusal

**Pakeitimas už leistinos apimties ribų**

- **Kas nutiko:** Failo keitimas už leistinos apimties ribų buvo užblokuotas. Už ribų niekas nepaliesta.
- **Kas galbūt jau įvyko:** Niekas nebuvo išsiųsta, pasirašyta ar pakeista
- **Ką daryti toliau:** Jei šis pakeitimas reikalingas, prašykite jo aiškiai, kad jis būtų leidžiamoje apimtyje.
- **Bandymas dar kartą:** Nesiųskite tos pačios užklausos dar kartą nepakeistos.
- **Rimtumas:** didelis · **Kategorija:** saugumas · **Svarbu saugumui:** taip

<a name="credential-failure"></a>

### credential_failure

**Įrašyto rakto nepavyko perskaityti**

- **Kas nutiko:** Įrašyto rakto nepavyko perskaityti. Įrašykite jį iš naujo Nustatymuose, kad galėtumėte tęsti.
- **Kas galbūt jau įvyko:** Niekas nebuvo išsiųsta, pasirašyta ar pakeista
- **Ką daryti toliau:** Seifo įrašo nepavyko iššifruoti; įrašykite kredencialą iš naujo arba patikrinkite seifo raktą.
- **Bandymas dar kartą:** Nesiųskite tos pačios užklausos dar kartą nepakeistos.
- **Rimtumas:** didelis · **Kategorija:** saugumas · **Svarbu saugumui:** taip

<a name="evidence-corruption"></a>

### evidence_corruption

**Atsakymo įrašai nepraėjo vientisumo patikros**

- **Kas nutiko:** Šio atsakymo pagrindą sudarantys įrašai nepraėjo vientisumo patikros, todėl atsakymą laikykite nepatikrintu.
- **Kas galbūt jau įvyko:** Įvyko vietinis įrašas ar pakeitimas
- **Ką daryti toliau:** Ištirkite šio ėjimo vykdymo knygą palyginti su vykdymo aplinkos įvykių srautu.
- **Bandymas dar kartą:** Nesiųskite tos pačios užklausos dar kartą nepakeistos.
- **Rimtumas:** didelis · **Kategorija:** vientisumas · **Svarbu saugumui:** taip

<a name="evm-pocket-custody-unavailable"></a>

### evm_pocket_custody_unavailable

**Senoji kišenė skirta tik Solanai**

- **Kas nutiko:** Senoji kišenė, t. y. atkūrimo fraze pagrįsta piniginė, laiko tik Solana raktą, todėl jos negalima sukurti ar atkurti EVM tinkle. Niekas nebuvo sukurta. EVM tinklui Nustatymuose, Kriptovaliutos, sukurkite VOOL piniginę tame tinkle, pridėkite paskyrą tik stebėjimui arba prijunkite savo EVM piniginę.
- **Kas galbūt jau įvyko:** Niekas nebuvo išsiųsta, pasirašyta ar pakeista
- **Ką daryti toliau:** Senoji kišenė užantspauduoja vieną Solana sėklą iš savo atkūrimo frazės. EVM tinkle naudokite Crypto Pilot piniginę („Create wallet“ tam tinklui Nustatymuose, Kriptovaliutos, Tinklai ir piniginės), tik stebėjimo paskyrą arba išorinę EVM piniginę (EIP-1193), kuri pati saugo savo raktą.
- **Bandymas dar kartą:** Nesiųskite tos pačios užklausos dar kartą nepakeistos.
- **Rimtumas:** mažas · **Kategorija:** politika

<a name="integrity-verification-failure"></a>

### integrity_verification_failure

**Įrašas nepraėjo apsaugos nuo klastojimo patikros**

- **Kas nutiko:** Įrašas nepraėjo nekėlimo patikros. Niekas nebuvo tylomis priimta.
- **Kas galbūt jau įvyko:** Įvyko vietinis įrašas ar pakeitimas
- **Ką daryti toliau:** Laikykite, kad pažeista kvitų grandinė yra kompromituota; ištirkite prieš pasitikėdami jos įrašais.
- **Bandymas dar kartą:** Nesiųskite tos pačios užklausos dar kartą nepakeistos.
- **Rimtumas:** kritinis · **Kategorija:** vientisumas · **Svarbu saugumui:** taip

<a name="permission-denied"></a>

### permission_denied

**Leidimas nesuteiktas**

- **Kas nutiko:** Šis veiksmas nebuvo leistas, todėl niekas nepasikeitė.
- **Kas galbūt jau įvyko:** Niekas nebuvo išsiųsta, pasirašyta ar pakeista
- **Ką daryti toliau:** Atverkite Nustatymus, pasirinkite Privatumas ir leidimai ir nustatykite „Ką VOOL gali daryti vienas“, kad pakeistumėte, kas valdo šį veiksmą.
- **Bandymas dar kartą:** Nesiųskite tos pačios užklausos dar kartą nepakeistos.
- **Rimtumas:** vidutinis · **Kategorija:** politika · **Svarbu saugumui:** taip

<a name="provider-exhausted"></a>

### provider_exhausted

**Pasiektas tiekėjo limitas**

- **Kas nutiko:** Modelio teikėjo naudojimo limitas išnaudotas. Pabandykite vėliau.
- **Kas galbūt jau įvyko:** Užklausa pasiekė išorinę paslaugą; joks mokėjimas nebuvo atliktas
- **Ką daryti toliau:** Patikrinkite sukonfigūruoto tiekėjo kvotas, dažnio ribas ir apmokėjimą.
- **Bandymas dar kartą:** Bandykite dar kartą vėliau: tai, kas sustabdė, laikui bėgant praeina.
- **Rimtumas:** vidutinis · **Kategorija:** prieinamumas

<a name="provider-unavailable"></a>

### provider_unavailable

**Tiekėjas nepasiekiamas**

- **Kas nutiko:** Modelio teikėjo nepavyko pasiekti. Pabandykite po akimirkos.
- **Kas galbūt jau įvyko:** Niekas nebuvo išsiųsta, pasirašyta ar pakeista
- **Ką daryti toliau:** Patikrinkite tiekėjo būseną, tinklo pasiekiamumą ir sukonfigūruotus adresus.
- **Bandymas dar kartą:** Bandykite dar kartą vėliau: tai, kas sustabdė, laikui bėgant praeina.
- **Rimtumas:** vidutinis · **Kategorija:** prieinamumas

<a name="timeout"></a>

### timeout

**Baigėsi laukimo laikas**

- **Kas nutiko:** Tai užtruko per ilgai ir buvo sustabdyta. Pakartojimas gali pavykti.
- **Kas galbūt jau įvyko:** Rezultatas nežinomas — prieš bandydami dar kartą patikrinkite būseną
- **Ką daryti toliau:** Jei laikmačiai kartojasi, patikrinkite tinklo delsas ir tiekėjo atsakumą.
- **Bandymas dar kartą:** Pakartojimas dabar gali pavykti.
- **Rimtumas:** vidutinis · **Kategorija:** prieinamumas

<a name="tool-unavailable"></a>

### tool_unavailable

**Įrankis neprieinamas**

- **Kas nutiko:** Šis įrankis šiuo metu neprieinamas, todėl ši dalis nebuvo atlikta. Prieš bandydami dar kartą, jį reikia įjungti.
- **Kas galbūt jau įvyko:** Niekas nebuvo išsiųsta, pasirašyta ar pakeista
- **Ką daryti toliau:** Atverkite Nustatymus, pasirinkite Privatumas ir leidimai ir nustatykite „Ką VOOL gali daryti vienas“ arba pasirinkite kitą kelią.
- **Bandymas dar kartą:** Bandykite dar kartą, kai atliksite šiame skyriuje nurodytą pakeitimą.
- **Rimtumas:** mažas · **Kategorija:** prieinamumas

<a name="unknown"></a>

### unknown

**Netikėta klaida**

- **Kas nutiko:** Šiame kompiuteryje apdorojant užklausą kažkas nutiko. Galite pabandyti dar kartą.
- **Kas galbūt jau įvyko:** Rezultatas nežinomas — prieš bandydami dar kartą patikrinkite būseną
- **Ką daryti toliau:** Žr. gedžio redaguotą priežasčių grandinę diagnostikoje; nežinoma priežastis niekada nekartojama akla.
- **Bandymas dar kartą:** Nesiųskite tos pačios užklausos dar kartą nepakeistos.
- **Rimtumas:** vidutinis · **Kategorija:** vidinė

<a name="unsupported-claim"></a>

### unsupported_claim

**Nepagrįsti teiginiai sulaikyti**

- **Kas nutiko:** Vienas ar keli teiginiai nepasitvirtino gautuose šaltiniuose ir buvo sulaikyti.
- **Kas galbūt jau įvyko:** Įvyko vietinis įrašas ar pakeitimas
- **Ką daryti toliau:** Veiksmo nereikia — tai grounding vartai, dirbantys savo darbą. Jei dažna, patikrinkite paieškos kokybę.
- **Bandymas dar kartą:** Nesiųskite tos pačios užklausos dar kartą nepakeistos.
- **Rimtumas:** vidutinis · **Kategorija:** vientisumas

<a name="wallet-acknowledgement-required"></a>

### wallet_acknowledgement_required

**Reikia patvirtinimo**

- **Kas nutiko:** Šiam žingsniui pirmiausia reikia jūsų patvirtinimo, todėl niekas nebuvo pasirašyta ar išsiųsta. Perskaitykite, ką piniginė sako apie šį veiksmą, tiksliai jį patvirtinkite ir bandykite dar kartą.
- **Kas galbūt jau įvyko:** Niekas nebuvo išsiųsta, pasirašyta ar pakeista
- **Ką daryti toliau:** Kai patvirtinimas suteiktų nuolatines teises leisti lėšas, pakeistų nuosavybę ar paleistų atnaujinimą, arba piniginė randa neatitikimą ar negali perskaityti užklausos, ji pirmiausia paprašo įvesti tikslų jos patvirtinimo tekstą; nieko bendro vietoj jo nepriimama.
- **Bandymas dar kartą:** Bandykite dar kartą, kai atliksite šiame skyriuje nurodytą pakeitimą.
- **Rimtumas:** mažas · **Kategorija:** politika

<a name="wallet-amount-invalid"></a>

### wallet_amount_invalid

**Suma netiksli šiai monetai**

- **Kas nutiko:** Ši suma nėra tiksli suma, kurią ši moneta gali turėti, todėl nieko nebuvo pasiūlyta. Pasitikrinkite sumą ir bandykite dar kartą.
- **Kas galbūt jau įvyko:** Niekas nebuvo išsiųsta, pasirašyta ar pakeista
- **Ką daryti toliau:** Siųskite paprastą dešimtainį dydį, ne tikslesnį, nei leidžia moneta, ir neviršijantį piloto vieno pavedimo ribos.
- **Bandymas dar kartą:** Bandykite dar kartą, kai atliksite šiame skyriuje nurodytą pakeitimą.
- **Rimtumas:** mažas · **Kategorija:** politika

<a name="wallet-approval-rejected"></a>

### wallet_approval_rejected

**Patvirtinimas nepriimtas**

- **Kas nutiko:** Šio mokėjimo patvirtinimas nebuvo priimtas, todėl niekas nebuvo pasirašyta ar išsiųsta.
- **Kas galbūt jau įvyko:** Niekas nebuvo išsiųsta, pasirašyta ar pakeista
- **Ką daryti toliau:** Patvirtinkite tikslius parametrus su savininko PIN arba biometrija; patvirtinimai nesikelia tarp skirtingų parametrų.
- **Bandymas dar kartą:** Nesiųskite tos pačios užklausos dar kartą nepakeistos.
- **Rimtumas:** vidutinis · **Kategorija:** politika · **Svarbu saugumui:** taip

<a name="wallet-backup-unavailable"></a>

### wallet_backup_unavailable

**Atsarginė kopija jau parodyta vieną kartą**

- **Kas nutiko:** Šios piniginės atsarginis raktas jau buvo parodytas vieną kartą, todėl jo parodyti vėl negalima. Jei jo neįrašėte, atšaukite sąranką ir į šį adresą nelėskite lėšų.
- **Kas galbūt jau įvyko:** Niekas nebuvo išsiųsta, pasirašyta ar pakeista
- **Ką daryti toliau:** Kriptovaliutų piloto atsarginė kopija atskleidžiama tiksliai vieną kartą sąrankos metu; antro atskleidimo ir eksportavimo nėra.
- **Bandymas dar kartą:** Nesiųskite tos pačios užklausos dar kartą nepakeistos.
- **Rimtumas:** didelis · **Kategorija:** saugumas · **Svarbu saugumui:** taip

<a name="wallet-broadcast-failed"></a>

### wallet_broadcast_failed

**Mokėjimo paskelbti nepavyko**

- **Kas nutiko:** Mokėjimo paskelbti nepavyko. Prieš bandydami dar kartą, patikrinkite jo būseną.
- **Kas galbūt jau įvyko:** Rezultatas nežinomas — prieš bandydami dar kartą patikrinkite būseną
- **Ką daryti toliau:** Patikrinkite tinklo RPC galinį tašką ir prašymo kvitą; išlaidos nebuvo užregistruotos kaip išsiųstos.
- **Bandymas dar kartą:** Bandykite dar kartą vėliau: tai, kas sustabdė, laikui bėgant praeina.
- **Rimtumas:** vidutinis · **Kategorija:** prieinamumas

<a name="wallet-caller-refused"></a>

### wallet_caller_refused

**Pradėta ne iš paties VOOL lango**

- **Kas nutiko:** Šį piniginės veiksmą galima pradėti tik iš VOOL lango šiame kompiuteryje, todėl niekas nebuvo pakeista, pasirašyta ar išsiųsta. Atverkite VOOL ir bandykite ten.
- **Kas galbūt jau įvyko:** Niekas nebuvo išsiųsta, pasirašyta ar pakeista
- **Ką daryti toliau:** Patikimus piniginės duris vadinkite iš patarnaujamo puslapio arba natyvaus lango; kitos kilmės puslapis, kadras ar įskiepis atmetamas.
- **Bandymas dar kartą:** Bandykite dar kartą, kai atliksite šiame skyriuje nurodytą pakeitimą.
- **Rimtumas:** didelis · **Kategorija:** saugumas · **Svarbu saugumui:** taip

<a name="wallet-card-data-refused"></a>

### wallet_card_data_refused

**Kortelės duomenys atmesti**

- **Kas nutiko:** Kortelių numeriai ir saugos kodai čia niekada saugomi; priimamas tik teikėjo tokenas. Niekas nebuvo įrašyta.
- **Kas galbūt jau įvyko:** Niekas nebuvo išsiųsta, pasirašyta ar pakeista
- **Ką daryti toliau:** Suokenizuokite kortelę pas tiekėją ir užregistruokite žetono nuorodą.
- **Bandymas dar kartą:** Nesiųskite tos pačios užklausos dar kartą nepakeistos.
- **Rimtumas:** didelis · **Kategorija:** saugumas · **Svarbu saugumui:** taip

<a name="wallet-chain-identity-mismatch"></a>

### wallet_chain_identity_mismatch

**Tinklo galutinio taško tapatybė neįrodyta**

- **Kas nutiko:** Tinklo galutinis taškas neįrodė tinklo, kuriuo prisistato, tapatybės, todėl niekas nebuvo pasirašyta ar išsiųsta.
- **Kas galbūt jau įvyko:** Užklausa pasiekė išorinę paslaugą; joks mokėjimas nebuvo atliktas
- **Ką daryti toliau:** Patikrinkite tinklo RPC galą; meluojantis chain id arba genesis atmetamas dar prieš bet kokį mokėjimą.
- **Bandymas dar kartą:** Nesiųskite tos pačios užklausos dar kartą nepakeistos.
- **Rimtumas:** didelis · **Kategorija:** saugumas · **Svarbu saugumui:** taip

<a name="wallet-confirmation-required"></a>

### wallet_confirmation_required

**Reikia įrašyto patvirtinimo**

- **Kas nutiko:** Tam žingsniui pirmiausia reikia įrašyto patvirtinimo, todėl niekas nepasikeitė. Patvirtinkite ir bandykite dar kartą.
- **Kas galbūt jau įvyko:** Niekas nebuvo išsiųsta, pasirašyta ar pakeista
- **Ką daryti toliau:** Perskaitykite įspėjimą ir įrašykite tikslią patvirtinimo frazę, kad tęstumėte.
- **Bandymas dar kartą:** Bandykite dar kartą, kai atliksite šiame skyriuje nurodytą pakeitimą.
- **Rimtumas:** mažas · **Kategorija:** politika

<a name="wallet-credential-mismatch"></a>

### wallet_credential_mismatch

**Įrašai nesutapo**

- **Kas nutiko:** Du įrašai nesutapo, todėl niekas nebuvo sukurta. Įrašykite tą patį PIN ar slaptažodį du kartus ir bandykite dar kartą.
- **Kas galbūt jau įvyko:** Niekas nebuvo išsiųsta, pasirašyta ar pakeista
- **Ką daryti toliau:** Kriptovaliutų piloto kredencialas patvirtinamas dar prieš atsirandant bet kokiai rakto medžiagai.
- **Bandymas dar kartą:** Bandykite dar kartą, kai atliksite šiame skyriuje nurodytą pakeitimą.
- **Rimtumas:** mažas · **Kategorija:** politika

<a name="wallet-dependency-unavailable"></a>

### wallet_dependency_unavailable

**EVM bibliotekos neįdiegtos**

- **Kas nutiko:** Ši versija negali atlikti EVM pasirašymo ar ABI kodavimo: šiame kompiuteryje nėra įdiegtos Ethereum bibliotekos. Niekas nebuvo pasirašyta ar išsiųsta. Solana, saugojimas ir išlaidų ribos nepaveiktos; įdiegus bibliotekas bandykite dar kartą.
- **Kas galbūt jau įvyko:** Niekas nebuvo išsiųsta, pasirašyta ar pakeista
- **Ką daryti toliau:** Įdiekite EVM papildinius (eth-abi, eth-utils, eth-account) EVM keliams įjungti; visos kitos piniginės funkcijos veikia ir be jų.
- **Bandymas dar kartą:** Bandykite dar kartą, kai atliksite šiame skyriuje nurodytą pakeitimą.
- **Rimtumas:** vidutinis · **Kategorija:** politika

<a name="wallet-device-auth-denied"></a>

### wallet_device_auth_denied

**Atrakinimas įrenginiu atmestas**

- **Kas nutiko:** Touch ID arba jūsų „Mac“ slaptažodis buvo atmestas arba neatidarė šios piniginės, todėl niekas nebuvo sukurta, pakeista, atrakinta, atskleista, pasirašyta ar išsiųsta. Bandykite dar kartą.
- **Kas galbūt jau įvyko:** Niekas nebuvo išsiųsta, pasirašyta ar pakeista
- **Ką daryti toliau:** Bandykite dar kartą ir patvirtinkite VOOL rodomą Touch ID arba „Mac“ slaptažodžio užklausą. Piniginė, kuri tvirtina Touch ID, neturi savo PIN ar slaptažodžio, kurį galėtų naudoti vietoj to. Jei užklausa vis nepavyksta, atkurkite piniginę iš jos atkūrimo frazės (Crypto Pilot – iš sąrankos metu parodytos atsarginės kopijos) ir suteikite jai PIN arba slaptažodį; privataus rakto eksportui vis tiek reikia Touch ID arba „Mac“ slaptažodžio.
- **Bandymas dar kartą:** Bandykite dar kartą, kai atliksite šiame skyriuje nurodytą pakeitimą.
- **Rimtumas:** mažas · **Kategorija:** politika

<a name="wallet-device-auth-unavailable"></a>

### wallet_device_auth_unavailable

**Atrakinimas įrenginiu nepasiekiamas**

- **Kas nutiko:** Touch ID arba jūsų „Mac“ slaptažodis šiai piniginei čia nepasiekiamas, todėl niekas nebuvo sukurta, pakeista, atrakinta, atskleista, pasirašyta ar išsiųsta. Bandykite dar kartą, kai jis bus pasiekiamas.
- **Kas galbūt jau įvyko:** Niekas nebuvo išsiųsta, pasirašyta ar pakeista
- **Ką daryti toliau:** Nauja piniginė vietoj to gali tvirtinti savo PIN arba slaptažodžiu. Piniginė, kuri tvirtina Touch ID, neturi savo PIN ar slaptažodžio: jei Touch ID ir „Mac“ slaptažodis lieka nepasiekiami, atkurkite ją iš jos atkūrimo frazės (Crypto Pilot – iš sąrankos metu parodytos atsarginės kopijos) ir suteikite jai PIN arba slaptažodį. Privataus rakto eksportas atsidaro tik su Touch ID arba „Mac“ slaptažodžiu, todėl atkurkite piniginę „Mac“ kompiuteryje, kuriame jie veikia, ir eksportuokite ten.
- **Bandymas dar kartą:** Bandykite dar kartą, kai atliksite šiame skyriuje nurodytą pakeitimą.
- **Rimtumas:** vidutinis · **Kategorija:** prieinamumas

<a name="wallet-disabled"></a>

### wallet_disabled

**Piniginė išjungta**

- **Kas nutiko:** Piniginė išjungta, todėl mokėjimas nebuvo pasiūlytas ar išsiųstas. Nustatymuose, Kriptovaliutos, įjunkite „Įjungti kriptovaliutas“, tada bandykite dar kartą.
- **Kas galbūt jau įvyko:** Niekas nebuvo išsiųsta, pasirašyta ar pakeista
- **Ką daryti toliau:** Įjunkite „Įjungti kriptovaliutas“ Nustatymuose, Kriptovaliutos, tik jei šiame diegime norite mokėjimų. Kai nustatytas kintamasis VOOL_WALLET_ENABLED, jis įjungia arba išjungia piniginę, kad ir ką rodytų jungiklis.
- **Bandymas dar kartą:** Bandykite dar kartą, kai atliksite šiame skyriuje nurodytą pakeitimą.
- **Rimtumas:** mažas · **Kategorija:** politika

<a name="wallet-duplicate-payment"></a>

### wallet_duplicate_payment

**Mokėjimas jau pasiūlytas ar išsiųstas**

- **Kas nutiko:** Šis mokėjimas jau buvo pasiūlytas ar išsiųstas, todėl antrą kartą jis nebuvo siunčiamas.
- **Kas galbūt jau įvyko:** Įvyko vietinis įrašas ar pakeitimas
- **Ką daryti toliau:** Patikrinkite esamo prašymo kvitą; iš tikrųjų naujam mokėjimui naudokite naują idempotencijos raktą.
- **Bandymas dar kartą:** Nesiųskite tos pačios užklausos dar kartą nepakeistos.
- **Rimtumas:** vidutinis · **Kategorija:** vientisumas · **Svarbu saugumui:** taip

<a name="wallet-environment-inactive"></a>

### wallet_environment_inactive

**Netinkama tinklo aplinka**

- **Kas nutiko:** Šis tinklas priklauso kitai tinklo aplinkai (Mainnet arba Testiniai tinklai), todėl nieko nebuvo sukurta, pasiūlyta ar pasirašyta. Persijunkite Nustatymuose, Kripto, Kūrėjų parinktys, tada bandykite dar kartą.
- **Kas galbūt jau įvyko:** Niekas nebuvo išsiųsta, pasirašyta ar pakeista
- **Ką daryti toliau:** Pasirinkite tinklo aplinką sąmoningai Kripto nustatymuose; paskyros, balansai ir kvitai niekada nekeliauja tarp aplinkų.
- **Bandymas dar kartą:** Bandykite dar kartą, kai atliksite šiame skyriuje nurodytą pakeitimą.
- **Rimtumas:** vidutinis · **Kategorija:** saugumas · **Svarbu saugumui:** taip

<a name="wallet-export-pin-not-accepted"></a>

### wallet_export_pin_not_accepted

**Eksportas atrakinamas tik įrenginio autentifikavimu**

- **Kas nutiko:** Eksportas niekada neatrakinamas PIN, slaptažodžiu ar slaptafraze, tik įrenginio autentifikavimu (Touch ID arba jūsų „Mac“ slaptažodžiu), todėl niekas nebuvo atskleista. Bandykite dar kartą be PIN ar slaptažodžio.
- **Kas galbūt jau įvyko:** Niekas nebuvo išsiųsta, pasirašyta ar pakeista
- **Ką daryti toliau:** Eksportas tyčia atrakinamas tik įrenginio autentifikavimu; PIN, slaptažodis ar slaptafrazė užklausoje atmetami dar prieš juos patikrinant.
- **Bandymas dar kartą:** Bandykite dar kartą, kai atliksite šiame skyriuje nurodytą pakeitimą.
- **Rimtumas:** didelis · **Kategorija:** saugumas · **Svarbu saugumui:** taip

<a name="wallet-export-refused"></a>

### wallet_export_refused

**Senasis eksportavimo būdas nebenaudojamas**

- **Kas nutiko:** Šis privataus rakto eksportavimo būdas nebenaudojamas, todėl niekas nebuvo atskleista. Piniginė, kurią galima eksportuoti, turi mygtuką „Export private key“ Nustatymuose, Kriptovaliutos, ir jis atsidaro tik su Touch ID arba jūsų „Mac“ slaptažodžiu.
- **Kas galbūt jau įvyko:** Niekas nebuvo išsiųsta, pasirašyta ar pakeista
- **Ką daryti toliau:** Naudokite „Export private key“ Nustatymuose, Kriptovaliutos, Kitos paskyros: eksportuojama tik senoji kišenė ir tik su Touch ID arba „Mac“ slaptažodžiu. Crypto Pilot piniginė niekada neeksportuojama; ji atkuriama iš sąrankos metu parodytos atsarginės kopijos.
- **Bandymas dar kartą:** Nesiųskite tos pačios užklausos dar kartą nepakeistos.
- **Rimtumas:** didelis · **Kategorija:** saugumas · **Svarbu saugumui:** taip

<a name="wallet-export-unavailable"></a>

### wallet_export_unavailable

**Eksportas šiai piniginei neprieinamas**

- **Kas nutiko:** Šios piniginės taip eksportuoti negalima: ji skirta tik stebėjimui arba šiame kompiuteryje nelaiko jokio rakto, jos vienintelė atsarginė kopija buvo parodyta sąrankos metu ir ji niekada neeksportuojama, VOOL neeksportuoja į jūsų pasirinktą piniginės programą, arba tai senoji kišenė iš ankstesnės versijos, kurią prieš eksportuojant į MetaMask reikia atkurti iš jos atkūrimo frazės. Niekas nebuvo atskleista.
- **Kas galbūt jau įvyko:** Niekas nebuvo išsiųsta, pasirašyta ar pakeista
- **Ką daryti toliau:** Eksportuojama tik šiame kompiuteryje užantspauduota senoji kišenė, į Phantom arba MetaMask, ir tik su Touch ID arba „Mac“ slaptažodžiu. Crypto Pilot piniginė atkuriama iš sąrankos metu parodytos atsarginės kopijos. Senoji kišenė iš ankstesnės versijos eksportuojama į MetaMask, kai ji atkuriama iš savo atkūrimo frazės.
- **Bandymas dar kartą:** Nesiųskite tos pačios užklausos dar kartą nepakeistos.
- **Rimtumas:** didelis · **Kategorija:** saugumas · **Svarbu saugumui:** taip

<a name="wallet-insufficient-funds"></a>

### wallet_insufficient_funds

**Balansas nepadengia sumos ir komisinių**

- **Kas nutiko:** Balansas neapima sumos ir didžiausios galimos šio tinklo komisinių ribos, todėl niekas nebuvo paruošta. Sumažinkite sumą arba papildykite lėšų ir bandykite dar kartą.
- **Kas galbūt jau įvyko:** Užklausa pasiekė išorinę paslaugą; joks mokėjimas nebuvo atliktas
- **Ką daryti toliau:** Išleidžiama suma neįskaito kitų vykstančių rezervacijų; Solanos liekana turi būti be nulių gale ir bent lygi nuomai.
- **Bandymas dar kartą:** Bandykite dar kartą, kai atliksite šiame skyriuje nurodytą pakeitimą.
- **Rimtumas:** mažas · **Kategorija:** politika

<a name="wallet-legacy-surface-retired"></a>

### wallet_legacy_surface_retired

**Senasis pinigų kelias nebenaudojamas**

- **Kas nutiko:** Šis pinigų kelias išretintas: kiekvienas mokėjimas eina per piniginės pasiūlymo ir patvirtinimo eigą. Niekas nebuvo pasirašyta ar išsiųsta.
- **Kas galbūt jau įvyko:** Niekas nebuvo išsiųsta, pasirašyta ar pakeista
- **Ką daryti toliau:** Naudokite piniginės įrankius (wallet.propose) arba piniginės API; senasis pasirašytojas, leidėjas ir eksportuotojas nebepatarnauja.
- **Bandymas dar kartą:** Nesiųskite tos pačios užklausos dar kartą nepakeistos.
- **Rimtumas:** didelis · **Kategorija:** saugumas · **Svarbu saugumui:** taip

<a name="wallet-limit-exceeded"></a>

### wallet_limit_exceeded

**Viršytas išlaidų limitas**

- **Kas nutiko:** Šis mokėjimas viršija išlaidų limitą, todėl jis nebuvo išsiųstas.
- **Kas galbūt jau įvyko:** Niekas nebuvo išsiųsta, pasirašyta ar pakeista
- **Ką daryti toliau:** Prieš kartodami peržiūrėkite vieno pavedimo, dienos ir vieno gavėjo ribas.
- **Bandymas dar kartą:** Nesiųskite tos pačios užklausos dar kartą nepakeistos.
- **Rimtumas:** vidutinis · **Kategorija:** politika · **Svarbu saugumui:** taip

<a name="wallet-network-disabled"></a>

### wallet_network_disabled

**Tinklas nedeklaruotas šiai piniginei**

- **Kas nutiko:** Šis tinklas nėra deklaruojamas šiai piniginei šiam veiksmui, todėl nieko nebuvo sukurta ar išsiųsta.
- **Kas galbūt jau įvyko:** Niekas nebuvo išsiųsta, pasirašyta ar pakeista
- **Ką daryti toliau:** Naudokite vieną iš paskelbtų tinklo eilučių; nepaskelbti tinklai, pseudonimai ir panašūs pavadinimai atmetami registre.
- **Bandymas dar kartą:** Nesiųskite tos pačios užklausos dar kartą nepakeistos.
- **Rimtumas:** didelis · **Kategorija:** saugumas · **Svarbu saugumui:** taip

<a name="wallet-not-found"></a>

### wallet_not_found

**Piniginė, pasiūlymas ar gavėjas neatpažintas**

- **Kas nutiko:** Tokia piniginė, pasiūlymas ar gavėjas neatpažintas, todėl niekas nebuvo daroma. Pasitikrinkite ir bandykite dar kartą.
- **Kas galbūt jau įvyko:** Niekas nebuvo išsiųsta, pasirašyta ar pakeista
- **Ką daryti toliau:** Patikrinkite piniginės id, prašymo id arba gavėjo adresą ir bandykite vėl.
- **Bandymas dar kartą:** Bandykite dar kartą, kai atliksite šiame skyriuje nurodytą pakeitimą.
- **Rimtumas:** mažas · **Kategorija:** politika

<a name="wallet-outbound-refused"></a>

### wallet_outbound_refused

**Užklausa už patvirtintų galutinių taškų ribų**

- **Kas nutiko:** Šiai užklausai nebuvo leista išeiti už piniginės patvirtintų galutinių taškų, todėl ryšys nebuvo užmegztas.
- **Kas galbūt jau įvyko:** Niekas nebuvo išsiųsta, pasirašyta ar pakeista
- **Ką daryti toliau:** Patikrinkite paskelbtas RPC kilmės ir mokėjimo išteklių politiką; privatūs, metaduomenų ir nepatvirtinti mazgai atmetami.
- **Bandymas dar kartą:** Nesiųskite tos pačios užklausos dar kartą nepakeistos.
- **Rimtumas:** didelis · **Kategorija:** saugumas · **Svarbu saugumui:** taip

<a name="wallet-password-invalid"></a>

### wallet_password_invalid

**Netinkamas piniginės slaptažodis**

- **Kas nutiko:** Slaptažodis neatitiko politikos arba neatrakino piniginės, todėl niekas nebuvo pasirašyta ar pakeista. Patikrinkite slaptažodį ir bandykite dar kartą.
- **Kas galbūt jau įvyko:** Niekas nebuvo išsiųsta, pasirašyta ar pakeista
- **Ką daryti toliau:** Piniginės slaptažodžiai turi nuo 10 iki 128 simbolių ir nėra vien skaitmenys. Neteisingas slaptažodis palieka raktą užantspauduotą ir skaičiuojamas piniginės užrakinimui po per daug neteisingų bandymų.
- **Bandymas dar kartą:** Bandykite dar kartą, kai atliksite šiame skyriuje nurodytą pakeitimą.
- **Rimtumas:** mažas · **Kategorija:** politika

<a name="wallet-paykit-unavailable"></a>

### wallet_paykit_unavailable

**Solana papildinys pay neįdiegtas**

- **Kas nutiko:** Ši versija negali apmokėti Solana x402 ar MPP pasiūlymų: šiame kompiuteryje neįdiegtas pasirenkamas papildinys pay (Solana pay-kit, Python 3.11 ar naujesnis). Niekas nebuvo pasirašyta ar išsiųsta. Visos kitos piniginės funkcijos veikia ir be jo; jį įdiegę bandykite dar kartą.
- **Kas galbūt jau įvyko:** Niekas nebuvo išsiųsta, pasirašyta ar pakeista
- **Ką daryti toliau:** Įdiekite pasirenkamą VOOL papildinį pay su Python 3.11 ar naujesniu, kad įjungtumėte pay-kit mokėjimus; piniginė bet kuriuo atveju išlaiko savo pasirašytoją, išlaidų ribas ir patvirtinimą.
- **Bandymas dar kartą:** Bandykite dar kartą, kai atliksite šiame skyriuje nurodytą pakeitimą.
- **Rimtumas:** vidutinis · **Kategorija:** politika

<a name="wallet-payment-channel-off"></a>

### wallet_payment_channel_off

**Mokėjimo kanalas išjungtas**

- **Kas nutiko:** Šis mokėjimas eina per išjungtą mokėjimo kanalą, todėl niekas nebuvo pasirašyta ar išsiųsta. Įjunkite kanalą Nustatymuose, Kriptovaliutos, Mokėjimo kanalai, tada bandykite dar kartą.
- **Kas galbūt jau įvyko:** Niekas nebuvo išsiųsta, pasirašyta ar pakeista
- **Ką daryti toliau:** Įjunkite mokėjimo kanalą Kripto nustatymuose tik jei norite, kad mokėjimai eitų per jį; riba ir jūsų patvirtinimas galioja kiekviename kanale.
- **Bandymas dar kartą:** Bandykite dar kartą, kai atliksite šiame skyriuje nurodytą pakeitimą.
- **Rimtumas:** mažas · **Kategorija:** politika

<a name="wallet-pin-invalid"></a>

### wallet_pin_invalid

**Netinkamas PIN**

- **Kas nutiko:** PIN neatitiko politikos arba neatrakino piniginės, todėl niekas nebuvo pasirašyta. Patikrinkite PIN ir bandykite dar kartą.
- **Kas galbūt jau įvyko:** Niekas nebuvo išsiųsta, pasirašyta ar pakeista
- **Ką daryti toliau:** Naudokite 6–12 skaitmenų PIN; neteisingas PIN palieka užantspauduotą raktą nepaliestą.
- **Bandymas dar kartą:** Bandykite dar kartą, kai atliksite šiame skyriuje nurodytą pakeitimą.
- **Rimtumas:** mažas · **Kategorija:** politika

<a name="wallet-quote-expired"></a>

### wallet_quote_expired

**Numatymas baigė galioti arba buvo pakeistas**

- **Kas nutiko:** Šis perkėlimo numatymas baigė galioti arba buvo pakeistas, todėl jo patvirtinti negalima ir niekas nebuvo išsiųsta. Atnaujinkite numatymą ir bandykite dar kartą.
- **Kas galbūt jau įvyko:** Niekas nebuvo išsiųsta, pasirašyta ar pakeista
- **Ką daryti toliau:** Pasiūlymas gyja trumpą langą ir yra pakeičiamas atnaujinimu arba aplinkos perjungimu; patvirtinimas visada suriša dabartinį.
- **Bandymas dar kartą:** Bandykite dar kartą, kai atliksite šiame skyriuje nurodytą pakeitimą.
- **Rimtumas:** mažas · **Kategorija:** politika

<a name="wallet-quote-mismatch"></a>

### wallet_quote_mismatch

**Patvirtinimas nebeatitinka peržiūros**

- **Kas nutiko:** Patvirtinimas nebeatitinka perkėlimo, kuris buvo parodytas, todėl niekas nebuvo pasirašyta ar išsiųsta. Pradėkite iš naujo nuo šviežio numatymo.
- **Kas galbūt jau įvyko:** Niekas nebuvo išsiųsta, pasirašyta ar pakeista
- **Ką daryti toliau:** Patvirtinimas suriša vieno pasiūlymo kontrolinę sumą; pasikeitusi paskyra, grandinė, gavėjas, suma, aplinka ar mokestis reiškia naują patvirtinimą.
- **Bandymas dar kartą:** Nesiųskite tos pačios užklausos dar kartą nepakeistos.
- **Rimtumas:** didelis · **Kategorija:** saugumas · **Svarbu saugumui:** taip

<a name="wallet-quote-unavailable"></a>

### wallet_quote_unavailable

**Balansas ar komisiniai nežinomi**

- **Kas nutiko:** Tinklas nedavė pilno atsakymo apie šio perkėlimo balansą ar komisinius, todėl patvirtinimas nebuvo ruošiamas ir niekas nebuvo išsiųsta. Pabandykite po akimirkos.
- **Kas galbūt jau įvyko:** Užklausa pasiekė išorinę paslaugą; joks mokėjimas nebuvo atliktas
- **Ką daryti toliau:** Nežinomi grandinės duomenys niekada nelaikomi nuliu; patikrinkite eilutės galą ir jo tapatybės įrodymą.
- **Bandymas dar kartą:** Bandykite dar kartą vėliau: tai, kas sustabdė, laikui bėgant praeina.
- **Rimtumas:** vidutinis · **Kategorija:** prieinamumas

<a name="wallet-recipient-refused"></a>

### wallet_recipient_refused

**Gavėjas negali to saugiai priimti**

- **Kas nutiko:** Šis gavėjas šiame tinkle negali saugiai priimti šio perkėlimo, todėl niekas nebuvo paruošta. Patikrinkite adresą ir bandykite dar kartą.
- **Kas galbūt jau įvyko:** Niekas nebuvo išsiųsta, pasirašyta ar pakeista
- **Ką daryti toliau:** Atmesta: nauja Solanos paskyra žemiau nuomos minimumo, vykdomoji ar ne kreivės paskyra, bloga EIP-55 kontrolinė suma, nulinis adresas, iš anksto sukompiliuoti ir sisteminiai kontraktai.
- **Bandymas dar kartą:** Bandykite dar kartą, kai atliksite šiame skyriuje nurodytą pakeitimą.
- **Rimtumas:** vidutinis · **Kategorija:** politika

<a name="wallet-recovery-refused"></a>

### wallet_recovery_refused

**Atkūrimo įrodymas netinka šiai piniginei**

- **Kas nutiko:** Šis atkūrimo įrodymas netinka šiai piniginei, todėl niekas nepasikeitė: įrašytas raktas turi būti atsarginė kopija, kurią VOOL rodė būtent šiam adresui, tokia forma, kokia ji buvo parodyta. Pasitikrinkite ir bandykite dar kartą arba naudokite kitus variantus.
- **Kas galbūt jau įvyko:** Niekas nebuvo išsiųsta, pasirašyta ar pakeista
- **Ką daryti toliau:** Atkūrimas iš naujo užantspauduoja tik esamą piniginę ir tik iš atsarginės kopijos, kuri iššifruoja į tos piniginės patį raktą (abi kryptys).
- **Bandymas dar kartą:** Bandykite dar kartą, kai atliksite šiame skyriuje nurodytą pakeitimą.
- **Rimtumas:** didelis · **Kategorija:** saugumas · **Svarbu saugumui:** taip

<a name="wallet-request-ambiguous"></a>

### wallet_request_ambiguous

**Tinka daugiau nei viena paskyra ar tinklas**

- **Kas nutiko:** Šiam perkėlimui tinka daugiau nei viena paskyra ar tinklas, todėl nieko nebuvo pasiūlyta. Nurodykite, kurią paskyrą ar tinklą turite omenyje, ir bandykite dar kartą.
- **Kas galbūt jau įvyko:** Niekas nebuvo išsiųsta, pasirašyta ar pakeista
- **Ką daryti toliau:** Pervedimas įvardija vieną paskyrą viename tinkle; kandidatai išvardyti gedžio kontekste.
- **Bandymas dar kartą:** Bandykite dar kartą, kai atliksite šiame skyriuje nurodytą pakeitimą.
- **Rimtumas:** mažas · **Kategorija:** politika

<a name="wallet-security-key-needed"></a>

### wallet_security_key_needed

**Reikia saugos rakto**

- **Kas nutiko:** Šiai piniginei reikia ir jos saugos rakto, bet raktas neatsakė: jis neįjungtas, tai kitas raktas arba jo nepalietėte laiku. Niekas nebuvo atrakinta, pakeista, pasirašyta ar išsiųsta. Įjunkite šios piniginės saugos raktą, palieskite jį, kai jis mirksės, ir bandykite dar kartą.
- **Kas galbūt jau įvyko:** Niekas nebuvo išsiųsta, pasirašyta ar pakeista
- **Ką daryti toliau:** Įjunkite prie šios piniginės pridėtą saugos raktą ir palieskite jį, kai jis mirksės. Jei raktą pametėte, atkurkite piniginę jos atsarginiu raktu (užrašytu ant popieriaus, Keychain ar USB atmintinėje): taip nustatomas naujas PIN, o pamestas raktas pašalinamas iš piniginės.
- **Bandymas dar kartą:** Bandykite dar kartą, kai atliksite šiame skyriuje nurodytą pakeitimą.
- **Rimtumas:** mažas · **Kategorija:** prieinamumas

<a name="wallet-send-needs-vool-wallet"></a>

### wallet_send_needs_vool_wallet

**Siuntimui reikia VOOL Wallet**

- **Kas nutiko:** Ši paskyra negali siųsti iš VOOL šiame tinkle: čia siunčia tik šiame įrenginyje sukurta VOOL Wallet. Prijungta ar tik stebėjimo paskyra vis tiek gali gauti lėšas ir rodyti savo likutį. Niekas nebuvo pasirašyta ar išsiųsta. Norėdami siųsti, pasirinkite VOOL Wallet šiame įrenginyje ir bandykite dar kartą.
- **Kas galbūt jau įvyko:** Niekas nebuvo išsiųsta, pasirašyta ar pakeista
- **Ką daryti toliau:** Siųskite iš VOOL Wallet šiame įrenginyje arba iš prijungtos piniginės programos; šiame tinkle VOOL nieko nepasirašo už prijungtą ar tik stebėjimo paskyrą.
- **Bandymas dar kartą:** Bandykite dar kartą, kai atliksite šiame skyriuje nurodytą pakeitimą.
- **Rimtumas:** mažas · **Kategorija:** politika

<a name="wallet-setup-state-invalid"></a>

### wallet_setup_state_invalid

**Sąrankos žingsnis dabar netaikomas**

- **Kas nutiko:** Šis sąrankos žingsnis šiuo metu šiai piniginei netaikomas, todėl niekas nepasikeitė. Patikrinkite piniginės sąrankos būseną ir bandykite dar kartą.
- **Kas galbūt jau įvyko:** Niekas nebuvo išsiųsta, pasirašyta ar pakeista
- **Ką daryti toliau:** Sąranka eina: generuojama, laukiama atsarginės, atsarginė atskleista, paruošta; atšaukimas ir tęsimas niekada neatskleidžia atsarginės kopijos antrą kartą.
- **Bandymas dar kartą:** Bandykite dar kartą, kai atliksite šiame skyriuje nurodytą pakeitimą.
- **Rimtumas:** mažas · **Kategorija:** politika

<a name="wallet-signature-invalid"></a>

### wallet_signature_invalid

**Parašas neatitiko piniginės rakto**

- **Kas nutiko:** Šiam mokėjimui grąžintas parašas neatitiko piniginės rakto, todėl jis nebuvo paskelbtas.
- **Kas galbūt jau įvyko:** Užklausa pasiekė išorinę paslaugą; joks mokėjimas nebuvo atliktas
- **Ką daryti toliau:** Patikrinkite prijungtą pasirašytoją; nesutampantis parašas atmetamas dar prieš transliavimą.
- **Bandymas dar kartą:** Nesiųskite tos pačios užklausos dar kartą nepakeistos.
- **Rimtumas:** didelis · **Kategorija:** saugumas · **Svarbu saugumui:** taip

<a name="wallet-signing-unavailable"></a>

### wallet_signing_unavailable

**Piniginė negali iš čia pasirašyti**

- **Kas nutiko:** Ši piniginė negali iš čia pasirašyti: ji tik stebėjimo arba jos išorinis pasirašytojas neprijungtas. Niekas nebuvo išsiųsta; prijunkite pasirašytoją ir bandykite dar kartą.
- **Kas galbūt jau įvyko:** Niekas nebuvo išsiųsta, pasirašyta ar pakeista
- **Ką daryti toliau:** Prijunkite išorinę piniginę arba Nustatymuose, Kriptovaliutos, sukurkite VOOL piniginę tame tinkle.
- **Bandymas dar kartą:** Bandykite dar kartą, kai atliksite šiame skyriuje nurodytą pakeitimą.
- **Rimtumas:** vidutinis · **Kategorija:** politika

<a name="wallet-simulation-failed"></a>

### wallet_simulation_failed

**Mokėjimas nepraėjo modeliavimo**

- **Kas nutiko:** Mokėjimas nepraėjo modeliavimo, todėl nebuvo išsiųstas. Patikrinkite balansą ir gavėją, tada bandykite dar kartą.
- **Kas galbūt jau įvyko:** Užklausa pasiekė išorinę paslaugą; joks mokėjimas nebuvo atliktas
- **Ką daryti toliau:** Patikrinkite balansą, gavėją ir tinklo RPC galinį tašką, tada teikite vėl.
- **Bandymas dar kartą:** Bandykite dar kartą vėliau: tai, kas sustabdė, laikui bėgant praeina.
- **Rimtumas:** vidutinis · **Kategorija:** prieinamumas

<a name="wallet-storage-class-refused"></a>

### wallet_storage_class_refused

**Raktų saugojimas tik atmintyje**

- **Kas nutiko:** Ši diegtis laiko įrenginio raktą tik atmintyje, todėl čia sukurta piniginė po perstartavimo negalėtų būti atidaryta. Niekas nebuvo sukurta. Perjunkite raktų saugojimą į pastovų režimą ir bandykite dar kartą.
- **Kas galbūt jau įvyko:** Niekas nebuvo išsiųsta, pasirašyta ar pakeista
- **Ką daryti toliau:** Kriptovaliutų piloto piniginės atmetamos, kol VOOL_KEY_STORAGE_MODE yra ephemeral; naudokite file arba keyring saugojimą.
- **Bandymas dar kartą:** Bandykite dar kartą, kai atliksite šiame skyriuje nurodytą pakeitimą.
- **Rimtumas:** vidutinis · **Kategorija:** politika

<a name="wallet-unlock-throttled"></a>

### wallet_unlock_throttled

**Piniginė užrakinta po nepavykusių atrakinimų**

- **Kas nutiko:** Buvo per daug neteisingų PIN ar slaptažodžio bandymų, todėl ši piniginė kurį laiką užrakinta ir niekas nebuvo atrakinta. Palaukite ir bandykite dar kartą.
- **Kas galbūt jau įvyko:** Niekas nebuvo išsiųsta, pasirašyta ar pakeista
- **Ką daryti toliau:** Nepavykę atrakinimai skaičiuojami patvariai kiekvienai piniginei ir visai programai, per paleidimus iš naujo; užraktas kyla automatiškai.
- **Bandymas dar kartą:** Bandykite dar kartą vėliau: tai, kas sustabdė, laikui bėgant praeina.
- **Rimtumas:** didelis · **Kategorija:** saugumas · **Svarbu saugumui:** taip

<a name="wallet-x402-cap-exceeded"></a>

### wallet_x402_cap_exceeded

**Apmokamas išteklius virš automatinės ribos**

- **Kas nutiko:** Apmokamas išteklius paprašė daugiau nei automatinio mokėjimo riba, todėl jis nebuvo apmokėtas.
- **Kas galbūt jau įvyko:** Niekas nebuvo išsiųsta, pasirašyta ar pakeista
- **Ką daryti toliau:** Sąmoningai padidinkite VOOL_WALLET_X402_CAP_MINOR arba mokėkite už išteklių atskiru aiškiu prašymu.
- **Bandymas dar kartą:** Nesiųskite tos pačios užklausos dar kartą nepakeistos.
- **Rimtumas:** vidutinis · **Kategorija:** politika · **Svarbu saugumui:** taip

<a name="wallet-x402-sdk-unavailable"></a>

### wallet_x402_sdk_unavailable

**Papildinys x402 neįdiegtas**

- **Kas nutiko:** Ši versija negali apmokėti x402 pasiūlymų Base tinkle per x402 Foundation SDK: šiame kompiuteryje neįdiegtas pasirenkamas papildinys x402. Niekas nebuvo pasirašyta ar išsiųsta. Visos kitos piniginės funkcijos veikia ir be jo; jį įdiegę bandykite dar kartą.
- **Kas galbūt jau įvyko:** Niekas nebuvo išsiųsta, pasirašyta ar pakeista
- **Ką daryti toliau:** Įdiekite pasirenkamą VOOL papildinį x402, kad galėtumėte apmokėti x402 pasiūlymus Base tinkle iš Crypto Pilot piniginės; piniginė bet kuriuo atveju išlaiko savo pasirašytoją, išlaidų ribas ir patvirtinimą.
- **Bandymas dar kartą:** Bandykite dar kartą, kai atliksite šiame skyriuje nurodytą pakeitimą.
- **Rimtumas:** vidutinis · **Kategorija:** politika

<a name="x402-scheme-unavailable"></a>

### x402_scheme_unavailable

**Mokėjimo schema nepalaikoma**

- **Kas nutiko:** Šiam mokėjimo pasiūlymui reikia schemos, kurios ši piniginė negali įrodyti iki galo, todėl niekas nebuvo pasirašyta. Rinkitės pasiūlymą, kurio schema, tokenas ir tarpininkas patikrinti šiam tinklui.
- **Kas galbūt jau įvyko:** Niekas nebuvo išsiųsta, pasirašyta ar pakeista
- **Ką daryti toliau:** Naudokite pasiūlymą, kurio schema, žetonas ir tarpininkas patikrinti šiam tinklui; dalinis pasirašymo palaikymas čia netinka.
- **Bandymas dar kartą:** Bandykite dar kartą, kai atliksite šiame skyriuje nurodytą pakeitimą.
- **Rimtumas:** mažas · **Kategorija:** politika

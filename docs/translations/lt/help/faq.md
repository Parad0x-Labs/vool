---
description: Dažni klausimai apie VOOL — ar jis veikia be interneto, kur saugomas jūsų API raktas, ar jis nemokamas ir kokias sistemas palaiko.
translation: machine
source_sha256: 4366d0e907cc16c3206be768f984f5a525f4187024d16aae541a2ec8937e0aa0
---

# DUK

## Ar VOOL veikia be interneto?

Taip, su vietiniu modeliu, jūsų pokalbiams ir failams. Debesijos modeliams pagal apibrėžimą reikia tinklo
ryšio. Naujausia informacija, pavyzdžiui, orai ar kainos, gaunama iš viešųjų žiniatinklio paslaugų tik tada, kai įjungta „Interneto paieška“ (po naujo įdiegimo ji išjungta), ir niekada režimu „Tik lokaliai“.

## Ar man reikia debesijos rakto?

Ne. Programai naudoti pakanka vietinio modelio.

## Kur saugomas mano raktas?

Šifruotame faile VOOL aplanke „runtime home“ jūsų kompiuteryje, kurį gali perskaityti tik jūsų naudotojo
paskyra. Raktai, kuriuos VOOL jau laikė macOS Keychain Mac kompiuteryje, ten ir lieka. Raktai lieka jūsų
kompiuteryje. Žr. [Saugumo modelis](../trust/security.md#raktai).

## Ar Parad0x mato mano užklausas?

Ne vietiniu režimu ir ne debesijos režimu — debesijos užklausos keliauja iš jūsų kompiuterio jūsų
tiekėjui. Žr. [Duomenų tvarkymas](../trust/data-handling.md).

## Kodėl sistema mane įspėja, kai atidarau VOOL?

macOS programos pasirašytos ad hoc, bet dar nėra notarizuotos, todėl macOS paprašo patvirtinti pirmąjį
atidarymą. Veiksmai aprašyti skyriuje [Diegimas](../getting-started/install.md#pirmasis-atidarymas-mac-kompiuteryje). Žr.
[Leidimų būsena](../trust/release-status.md).

## Ar galiu naudoti savo modelį?

Taip. Bet kurią vykdymo aplinką, kuri teikia su OpenAI suderinamą vietinį galinį tašką. Žr.
[Palaikomi modeliai](../reference/models.md).

## Ar jis nemokamas?

Programa beta laikotarpiu nemokama. Už debesijos modelių naudojimą sąskaitą išrašo jūsų tiekėjas, jūsų
paskyrai.

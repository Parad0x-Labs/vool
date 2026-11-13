---
description: Kaip prijungti OpenRouter prie VOOL, kad vienas API raktas suteiktų prieigą prie daugelio debesijos DI modelių.
translation: machine
source_sha256: 75cb20d43eb37adb1d30913959ca1cb46864ad57ba7a1a22ec261f0e0eaf2e9a
---

# OpenRouter prijungimas

OpenRouter suteikia prieigą prie daugelio tiekėjų naudojant vieną raktą. VOOL palaiko OpenRouter rakto
pridėjimą.

## Rakto pridėjimas

Sukurkite raktą savo OpenRouter paskyroje, įklijuokite jį skiltyje **Nustatymai → API raktai** ir paspauskite
**Išsaugoti raktą**. VOOL vieną kartą patikrina raktą su OpenRouter, tada laiko jį šifruotame faile šiame
kompiuteryje (žr. [Saugumo modelis](../trust/security.md#raktai)).

## Atsiskaitymas ir priskyrimas

{% hint style="info" %}
Jūsų OpenRouter paskyra lieka jūsų. VOOL siunčia užklausas tiesiai iš šio kompiuterio
naudodamas jūsų raktą. Parad0x netarpininkauja jūsų užklausoms ir nemoka už jūsų naudojimą. Tikras VOOL
naudojimas gali atsispindėti viešoje suvestinėje VOOL statistikoje OpenRouter, tačiau atskiros užklausos
ir tapatybės neskelbiamos.
{% endhint %}

## Rakto pašalinimas

Pašalinkite raktą skiltyje **Nustatymai → API raktai**. Jei norite, kad raktas nebeveiktų ir serverio pusėje, atšaukite jį ir savo OpenRouter paskyroje.

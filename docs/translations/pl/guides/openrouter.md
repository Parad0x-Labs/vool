---
description: Jak podłączyć OpenRouter do VOOL, aby jeden klucz API dawał dostęp do wielu modeli AI w chmurze.
translation: machine
source_sha256: 75cb20d43eb37adb1d30913959ca1cb46864ad57ba7a1a22ec261f0e0eaf2e9a
---

# Podłączanie OpenRouter

OpenRouter daje dostęp do wielu dostawców za pomocą jednego klucza. VOOL obsługuje dodanie
klucza OpenRouter.

## Dodawanie klucza

Utwórz klucz na swoim koncie OpenRouter, wklej go w **Ustawienia → Klucze API** i naciśnij
**Zapisz klucz**. VOOL raz sprawdza klucz w OpenRouter, a potem przechowuje go w zaszyfrowanym pliku na
tym komputerze (zobacz [Model bezpieczeństwa](../trust/security.md#klucze)).

## Rozliczenia i atrybucja

{% hint style="info" %}
Twoje konto OpenRouter pozostaje twoje. VOOL wysyła żądania bezpośrednio z tego komputera
z użyciem twojego klucza. Parad0x nie pośredniczy w przesyłaniu twoich promptów ani nie płaci za twoje użycie. Rzeczywiste
użycie VOOL może pojawiać się w publicznych, zbiorczych statystykach VOOL w OpenRouter, ale pojedyncze prompty
i tożsamości nie są publikowane.
{% endhint %}

## Usuwanie klucza

Usuń klucz w **Ustawienia → Klucze API**. Unieważnij też klucz na swoim koncie OpenRouter, jeśli chcesz, żeby przestał działać również po stronie serwera.

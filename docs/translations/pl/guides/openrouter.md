---
description: Jak podłączyć OpenRouter do VOOL, aby jeden klucz API dawał dostęp do wielu modeli AI w chmurze.
translation: machine
source_sha256: 3a09fe6c636e2b8dd14a2d627c4653055dbb10bf8fd8221b7a3bcccf3044cb19
---

# Podłączanie OpenRouter

OpenRouter daje dostęp do wielu dostawców za pomocą jednego klucza. VOOL obsługuje podłączenie
konta OpenRouter.

## Podłączanie

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

## Odłączanie

Usuń klucz w **Ustawienia → Klucze API**. Unieważnij też klucz na swoim koncie OpenRouter, jeśli chcesz, żeby przestał działać również po stronie serwera.

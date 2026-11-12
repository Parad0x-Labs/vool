---
description: Jakie dane opuszczają twój komputer, gdy korzystasz z VOOL, i kiedy. Różnica między uruchamianiem AI lokalnie a korzystaniem z modelu w chmurze.
translation: machine
source_sha256: 3217e13bf6d684853b92d19097627d07e64bdd17745560b1204871cb0b7b0753
---

# Lokalnie i w chmurze

VOOL ma dwa stany. Interfejs zawsze pokazuje, który z nich jest aktywny.

## Lokalnie

Wszystko zostaje na tym komputerze. Model działa w lokalnym środowisku uruchomieniowym, pliki są odczytywane
z dysku i do żadnego dostawcy nie trafia żadne żądanie.

## Chmura (nazwa dostawcy)

Wybrany kontekst jest wysyłany bezpośrednio do wybranego przez ciebie dostawcy, z twojego komputera,
z użyciem twojego klucza.

„Wybrany kontekst” oznacza to, co aplikacja zebrała dla danego żądania: twój prompt oraz
pliki i wcześniejsze tury, których to żądanie potrzebuje. Jest pokazywany przed wysłaniem.

{% hint style="danger" %}
Tryb chmury wysyła dane poza twój komputer. Na tym właśnie polega tryb chmury. Zapewnienie VOOL dotyczy
*drogi* — żądanie idzie z twojego komputera do twojego dostawcy — a nie tego, że
nic nie jest przesyłane.
{% endhint %}

## Co jest pośrodku

Nic, co obsługuje Parad0x Labs. Nie ma żadnego proxy, bramy ani przekaźnika dla ruchu do modeli.
Twój klucz jest używany przez aplikację na twoim komputerze do bezpośredniej komunikacji z dostawcą.

Wszystkie szczegóły: [Przetwarzanie danych](../trust/data-handling.md).

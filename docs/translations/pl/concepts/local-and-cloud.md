---
description: Jakie dane opuszczają twój komputer, gdy korzystasz z VOOL, i kiedy. Różnica między uruchamianiem AI lokalnie a korzystaniem z modelu w chmurze.
translation: machine
source_sha256: 2c2b9e26e99579c00a032dfeac95fd216fe9229497c965c1bc08aface7a6c071
---

# Lokalnie i w chmurze

VOOL może odpowiadać na dwa sposoby: na tym komputerze albo przez dostawcę w chmurze. Nagłówek czatu
pokazuje, w który sposób zostanie wykonane następne żądanie — na przykład **AUTO** z
**Najpierw lokalnie**, gdy VOOL Auto odpowiada z tego komputera.

## Lokalnie

Model i twoje pliki zostają na tym komputerze. Model działa w lokalnym środowisku uruchomieniowym, pliki są
odczytywane z dysku i do żadnego dostawcy nie trafia żadne żądanie. Bieżące informacje, takie jak pogoda czy ceny, pochodzą z publicznych usług internetowych tylko wtedy, gdy włączone jest „Wyszukiwanie w sieci” (po świeżej instalacji jest wyłączone), i nigdy w trybie „Tylko lokalnie”.

## Chmura (nazwa dostawcy)

Wybrany kontekst jest wysyłany bezpośrednio do wybranego przez ciebie dostawcy, z twojego komputera,
z użyciem twojego klucza.

„Wybrany kontekst” oznacza to, co aplikacja zebrała dla danego żądania: twój prompt oraz
pliki i wcześniejsze tury, których to żądanie potrzebuje.

{% hint style="danger" %}
Tryb chmury wysyła dane poza twój komputer. Na tym właśnie polega tryb chmury. Zapewnienie VOOL dotyczy
*drogi* — żądanie idzie z twojego komputera do twojego dostawcy — a nie tego, że
nic nie jest przesyłane.
{% endhint %}

## Co jest pośrodku

Nic, co obsługuje Parad0x Labs. Nie ma żadnego proxy, bramy ani przekaźnika dla ruchu do modeli.
Twój klucz jest używany przez aplikację na twoim komputerze do bezpośredniej komunikacji z dostawcą.

Wszystkie szczegóły: [Przetwarzanie danych](../trust/data-handling.md).

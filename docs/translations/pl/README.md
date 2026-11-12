---
description: VOOL to asystent AI na komputer, który uruchamia modele AI lokalnie na twoim komputerze albo łączy się z modelami w chmurze za pomocą twojego własnego klucza. Darmowy i prywatny. Pliki do pobrania dla Maca (Apple Silicon i Intel) oraz Linuksa; Windows jest eksperymentalny i instaluje się ze źródeł.
translation: machine
source_sha256: c31e5a047068e09c01bbd2e2c90cbbed0b70e78926847a5b737f9e0e625ab1dd
---

# VOOL

VOOL to asystent AI na komputer. Instaluje się na twoim komputerze jako aplikacja i może
uruchamiać modele AI lokalnie na tej maszynie albo łączyć się z dostawcą AI w chmurze za pomocą twojego
własnego klucza API. Prowadzi czytelny zapis każdej wykonanej czynności.

Jest darmowy i z modelami lokalnymi działa offline. Są pliki do pobrania dla Maców (Apple Silicon
i Intel) oraz Linuksa x86_64; Windows jest eksperymentalny i instaluje się ze źródeł.

Ta dokumentacja opisuje instalację VOOL, podłączanie modelu oraz zasady tego, jak VOOL
obchodzi się z twoimi plikami, twoją pamięcią i twoimi kluczami.

## Jak wypada w porównaniu

Na 150 nigdy wcześniej nieużywanych pytaniach pamięciowych LoCoMo, przy tym samym modelu odpowiadającym za każdego
agenta i tym samym oceniającym GPT-4o, pamięć VOOL pokonała dwa inne agenty open source:

| | VOOL | OpenClaw | Hermes |
| --- | --- | --- | --- |
| Poprawne odpowiedzi | **82,7%** (124 / 150) | 71,3% (107) | 62,7% (94) |
| Tokeny na pytanie | **9,6k** | 51,8k | 33,5k |
| Koszt pytania | **0,0033 USD** | 0,0100 USD | 0,0046 USD |

To 5,4 razy mniej tokenów niż OpenClaw i 3,5 razy mniej niż Hermes, przy jednej trzeciej kosztu odpowiedzi
OpenClaw. Pamięć dostarczana w VOOL 0.7 uzyskała na tych samych pytaniach 123 na 150.
Każdy przebieg, konfiguracja i słabe punkty są opublikowane: [testy porównawcze pamięci](https://github.com/Parad0x-Labs/vool/tree/main/docs/benchmarks).

## Zacznij tutaj

| Jeśli chcesz | Przeczytaj |
| --- | --- |
| Uruchomić VOOL | [Instalacja](getting-started/install.md) |
| Zrozumieć pierwszy ekran | [Pierwsze uruchomienie](getting-started/first-run.md) |
| Wskazać mu model | [Podłączanie modelu](getting-started/connect-a-model.md) |
| Kontrolować, jakie wydatki mogą wystąpić | [Limity wydatków](guides/spending-limits.md) |
| Wiedzieć, co opuszcza twój komputer | [Lokalnie i w chmurze](concepts/local-and-cloud.md) |

## Czym jest VOOL

* **Aplikacją na komputer.** Instaluje się na twoim komputerze — to nie karta przeglądarki ani
  usługa hostowana.
* **Działa offline.** Gdy wybrany jest model lokalny, VOOL nie potrzebuje połączenia z internetem.
* **Domyślnie lokalny.** Gdy wybrany jest model lokalny, prompty i pliki są przetwarzane
  na twoim komputerze.
* **Niezależny od modelu.** Można wybrać zarówno lokalne środowiska uruchomieniowe, jak i dostawców chmurowych. Klucz
  do chmury dostarczasz sam; zostaje na twoim komputerze.
* **Możliwy do sprawdzenia.** Każde wywołanie narzędzia jest zapisywane z danymi wejściowymi i wynikami, więc
  przebieg pracy można później odczytać.

{% hint style="warning" %}
VOOL jest w wersji beta dla Maców z Apple Silicon, Maców z procesorem Intel i Linuksa x86_64. Aplikacje
na Maca nie są jeszcze poświadczone notarialnie przez Apple, więc przy pierwszym otwarciu macOS prosi o potwierdzenie;
strona [Instalacja](getting-started/install.md#pierwsze-otwarcie-na-macu) pokazuje, jak to zrobić. Przed instalacją
przeczytaj [Stan wydań](trust/release-status.md).
{% endhint %}

## Dwa tryby, bez niedomówień

VOOL rozróżnia dokładnie dwa stany, a interfejs zawsze pokazuje, który z nich jest aktywny:

1. **Lokalnie** — praca zostaje na tym komputerze.
2. **Chmura** — wybrany kontekst jest wysyłany bezpośrednio do wybranego przez ciebie dostawcy,
   z twojego komputera, z użyciem twojego klucza.

Nie ma trzeciego stanu ani żadnego przekaźnika obsługiwanego przez Parad0x pośrodku. Szczegóły
są w sekcji [Przetwarzanie danych](trust/data-handling.md).

## Pomoc

* [FAQ](help/faq.md) — najczęstsze pytania
* [Rozwiązywanie problemów](help/troubleshooting.md) — gdy coś się nie uruchamia
* [Discord](https://discord.gg/V9NkjP3Fzz) — zapytaj zespół

---
description: Jak uruchamiać modele AI lokalnie na własnym komputerze z VOOL — instalacja lokalnego serwera modeli, wybór rozmiaru modelu i praca całkowicie offline.
translation: machine
source_sha256: a19d4790729d13ed5e1b134bcb67ed3e7a80609561be4227ab9480492100c8a6
---

# Uruchamianie modelu lokalnego

## Instalacja środowiska uruchomieniowego

VOOL domyślnie uruchamia modele lokalne za pomocą Ollamy; działają też inne lokalne środowiska uruchomieniowe
z punktem końcowym zgodnym z OpenAI. Ollama to osobny program, instalowany oddzielnie
z [ollama.com](https://ollama.com). Aplikacja na macOS ma własny program do uruchamiania modeli lokalnych,
więc na Macu możesz pominąć ten krok. W Linuksie i przy instalacjach ze źródeł zainstaluj Ollamę, uruchom
ją i pobierz model.

```bash
ollama serve
ollama pull qwen3:4b
```

VOOL domyślnie sam wybiera dla ciebie model lokalny — model Qwen dobrany do twojego komputera. Na
wielu komputerach jest to `qwen3:4b`; na komputerach z 32 GB pamięci RAM lub więcej domyślnie jest
to większy model Qwen.

## Wskazanie go VOOL

Otwórz **Ustawienia → Modele i dostawcy**. Działające lokalne środowiska uruchomieniowe są wykrywane automatycznie.
Wybierz model, a wskaźnik trybu zmieni się na Lokalnie.

## Jednorazowe przygotowanie

Model lokalny pisze odpowiedzi dopiero wtedy, gdy VOOL go sprawdzi. Otwórz
**Ustawienia → Modele i dostawcy → Modele lokalne** i naciśnij **Użyj tego modelu** obok modelu.
VOOL po cichu sprawdza na twoim komputerze, czy model korzysta z narzędzi VOOL wystarczająco dobrze, by
odpowiadać za ciebie. Sprawdzenie może potrwać minutę lub dwie; nic nie jest pobierane i z nikim nie następuje
kontakt. Gdy przejdzie, model pokazuje **Gotowy** i VOOL nim odpowiada. Do tego czasu VOOL nie pozwala mu pisać
odpowiedzi końcowych.

* **Nie pasuje:** model nie korzysta z narzędzi VOOL wystarczająco dobrze. VOOL podaje, co możesz zrobić
  zamiast tego: użyć lub pobrać model, który wybrał dla tego komputera, albo użyć modelu w chmurze.
* **Sprawdzanie nie zakończyło się:** upewnij się, że Ollama jest otwarta, a potem naciśnij **Spróbuj ponownie**.
  Wolny komputer może potrzebować drugiej próby.

Jeśli przy uruchomieniu VOOL żaden model lokalny nie jest gotowy, VOOL sprawdza do trzech modeli na tym
komputerze, zaczynając od wybranego przez ciebie, i używa pierwszego, który przejdzie. Pomija model za duży dla
tego komputera. Gotowy model zawsze ma pierwszeństwo przed niegotowym.

## Dobór rozmiaru

| Rozmiar modelu | Potrzebna pamięć RAM | Nadaje się do |
| --- | ---: | --- |
| 3B | ~4 GB | Krótkich edycji, szybkich pytań |
| 7–8B | ~8 GB | Ogólnego użytku, większości programowania |
| 14B | ~16 GB | Dłuższego rozumowania |
| 32B+ | 32 GB lub więcej | Cięższej pracy, wolniej |

{% hint style="info" %}
Wersje skwantyzowane znacznie zmniejszają zużycie pamięci. Model 7B w 4-bitowej kwantyzacji działa wygodnie
w około 5 GB.
{% endhint %}

## Jeśli działa wolno

* Wybierz mniejszy lub mocniej skwantyzowany model.
* Zamknij inne aplikacje zużywające dużo pamięci.
* Skróć kontekst — mniej plików w żądaniu to mniej do przetworzenia.

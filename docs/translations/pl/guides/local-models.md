---
description: Jak uruchamiać modele AI lokalnie na własnym komputerze z VOOL — instalacja lokalnego serwera modeli, wybór rozmiaru modelu i praca całkowicie offline.
translation: machine
source_sha256: d93b9995b0f4fec8aca12bb90d8d6f9ff0fd9623c43d4529913dc76f055ac24c
---

# Uruchamianie modelu lokalnego

## Instalacja środowiska uruchomieniowego

VOOL komunikuje się z lokalnym serwerem modeli. Aplikacja na macOS ma własny program do uruchamiania modeli
lokalnych, więc na Macu możesz pominąć ten krok. W Linuksie i przy instalacjach ze źródeł zainstaluj serwer,
taki jak Ollama, uruchom go i pobierz model.

```bash
ollama serve
ollama pull qwen2.5-coder:7b
```

## Wskazanie go VOOL

Otwórz **Ustawienia → Modele i dostawcy**. Działające lokalne środowiska uruchomieniowe są wykrywane automatycznie.
Wybierz model, a wskaźnik trybu zmieni się na Lokalnie.

## Jednorazowa certyfikacja

Model lokalny pisze odpowiedzi dopiero po certyfikacji. Otwórz **Ustawienia → Modele i dostawcy → Modele lokalne**,
zarejestruj model, a potem wybierz **Certify**. Certyfikacja uruchamia krótki test narzędzi na twoim
komputerze; nic nie jest pobierane i z nikim nie następuje kontakt. Dopóki model nie przejdzie testu, VOOL nie
pozwala mu pisać odpowiedzi końcowych.

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

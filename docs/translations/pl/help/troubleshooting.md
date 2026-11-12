---
description: Rozwiązania najczęstszych problemów.
translation: machine
source_sha256: 792c90fd68caf7409212d3742109c46e49d1f7e640aa3e9f70db9fe61ca55d77
---

# Rozwiązywanie problemów

## macOS nie chce otworzyć aplikacji

To normalne: aplikacja nie jest jeszcze poświadczona notarialnie. Najpierw sprawdź sumę kontrolną, a potem otwórz
**Ustawienia systemowe → Prywatność i ochrona** i kliknij **Otwórz mimo to**. W macOS 14 możesz zamiast tego
kliknąć VOOL, trzymając naciśnięty klawisz Control, i wybrać **Otwórz**.

## Windows

W tym wydaniu nie ma pliku do pobrania dla Windows. Na Windows VOOL instaluje się ze źródeł i jest to
wsparcie eksperymentalne; zobacz [instrukcję instalacji](../getting-started/install.md#instalacja-ze-źródeł).

## Nie widać żadnych modeli lokalnych

1. Upewnij się, że środowisko uruchomieniowe działa.
2. Upewnij się, że pobrano co najmniej jeden model.
3. Upewnij się, że port jest zgodny z tym, którego oczekuje VOOL.

```bash
curl http://localhost:11434/api/tags
```

Jeśli to polecenie nic nie zwróci, środowisko uruchomieniowe nie działa.

## Odpowiedzi są powolne

Zwykle chodzi o rozmiar modelu w stosunku do dostępnej pamięci RAM. Zobacz
[Uruchamianie modelu lokalnego](../../../guides/local-models.md).

## Zaczynanie od nowa

Zamknij VOOL i usuń katalog konfiguracji wskazany w sekcji
[Konfiguracja](../../../reference/configuration.md). Pliki obszaru roboczego pozostają nietknięte.

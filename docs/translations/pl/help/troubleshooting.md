---
description: Rozwiązania najczęstszych problemów.
translation: machine
source_sha256: 809bae11bc4bf8c2d707cfef616f80056a5d98d35b0786cc937a40526e802689
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
[Uruchamianie modelu lokalnego](../guides/local-models.md).

## Błąd pokazuje kod

Każdy błąd ma stały kod. Strona [Błędy i kody błędów](../reference/errors.md) wyjaśnia, jak go
odczytać, a [Księga błędów](../ERROR_BOOK.md) podaje dla każdego kodu, co się stało, co mogło się
już stać i co zrobić dalej.

## Zaczynanie od nowa

Zamknij VOOL i usuń folder „runtime home” wskazany w sekcji
[Konfiguracja](../reference/configuration.md). Spowoduje to usunięcie twoich czatów, pamięci,
ustawień, zapisanych kluczy i każdego portfela, który VOOL przechowuje na tym komputerze, więc
najpierw zapisz to, czego potrzebujesz: zobacz
[Kopia zapasowa i odzyskiwanie](backup-and-recovery.md). Pliki obszaru roboczego pozostają
nietknięte.

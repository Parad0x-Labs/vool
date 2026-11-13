---
description: Jak używać VOOL jako asystenta AI do programowania we własnym kodzie — ustawianie obszaru roboczego, pytania o kod i przeglądanie edycji.
translation: machine
source_sha256: 1d1c4e7c4222eed4dc6caae24dca70729496ead8b88b1fc45a81a61ed548241a
---

# Programowanie z VOOL

## Ustaw obszar roboczy na repozytorium

Skieruj [obszar roboczy](../concepts/workspaces.md) na katalog główny projektu. VOOL odczytuje
potrzebne pliki, zamiast wczytywać całe drzewo.

## Pytaj w kategoriach kodu

Podanie nazwy pliku lub symbolu daje środowisku uruchomieniowemu punkt wyjścia:

> Znajdź, gdzie w `src/auth/` ustawiany jest limit czasu sesji, i wyjaśnij, co się dzieje, gdy
> ten czas upłynie.

## Przejrzyj przed zastosowaniem

W trybie Ręczny każda edycja najpierw pyta. W trybie Auto codzienne edycje i polecenia mogą działać
automatycznie i pojawiają się w zapisie aktywności, natomiast usuwanie i nadpisywanie nadal pytają. Przed dużą
zmianą zrób commit, aby diff był łatwy do przeczytania i łatwy do cofnięcia.

## Lokalnie czy w chmurze

Programowanie działa w obu trybach. Lokalny model do programowania 7B radzi sobie z większością codziennych
edycji; większe modele w chmurze radzą sobie z dłuższym rozumowaniem. Wskaźnik modelu pokazuje, który z nich
jest aktywny, zanim wyślesz: `LOCAL` dla modelu lokalnego, `CLOUD` dla modelu w chmurze i domyślnie `AUTO`
(`VOOL Auto`, `Local first`), gdy wybiera VOOL.

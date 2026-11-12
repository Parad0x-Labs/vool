---
description: Narzędzia to czynności, które może wykonać VOOL, a każda z nich jest zapisywana.
translation: machine
source_sha256: 53722a7f30f4446b6d029465a402a823f60496a66008b5df150d201c1963b152
---

# Narzędzia

Narzędzie to czynność, którą środowisko uruchomieniowe może wykonać w twoim imieniu — odczytanie pliku, uruchomienie
polecenia, przeszukanie folderu.

## Możliwe do sprawdzenia z założenia

Każde wywołanie narzędzia zapisuje, o co poproszono, co zostało uruchomione i co zwróciło. Przebieg można
odczytać później bez ponownego uruchamiania.

## Uprawnienia

To, czy narzędzie uruchomi się od razu, czy najpierw zapyta, zależy od wybranego trybu i od czynności.
Nowy czat zaczyna w trybie Ręczny, w którym każda zmiana najpierw pyta. W trybie Auto tworzenie i edycja
plików, polecenia i działania sieciowe są wykonywane bez pytania, natomiast usuwanie, nadpisywanie,
instalowanie, commity i pushe Git, wdrażanie, wysyłanie wiadomości, płatności i zmiana
ustawień nadal pytają. W każdym trybie narzędzia plikowe działają tylko wewnątrz [obszaru roboczego](workspaces.md),
narzędzia wyszukiwania i pobierania odczytują zasoby publiczne, niczego nie zapisując, a zapisane sekrety
nigdy nie są odczytywane. Strona [Uprawnienia](permissions.md) opisuje wszystkie pięć trybów i to, na co pozwala
każdy przycisk zgody.

```text
tool: read_file
path: src/main.py
result: 412 lines
```

Zobacz też [Potwierdzenia](receipts.md).

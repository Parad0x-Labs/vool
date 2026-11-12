---
description: Jak VOOL się aktualizuje, co dzieje się z twoimi danymi i jak wygląda zgodność wersji.
translation: machine
source_sha256: 3b6a950556f4669f116dfa3a911ffb5e07ad8bcfa04415d68cf5a3d1adf0fe65
---

# Aktualizacje i migracja

## Jak działają aktualizacje

Od wersji 0.7.5 VOOL aktualizuje się sam: gdy włączone jest „Samodzielne łączenie z internetem”, pokazuje powiadomienie i instaluje nową wersję jednym naciśnięciem. Aktualizacja z 0.7.4 lub 0.7.3 na Macu: włącz „Samodzielne łączenie z internetem” (Ustawienia > Prywatność i uprawnienia) i raz naciśnij „Sprawdź aktualizacje”. Z 0.7.4 lub starszej na Linuksie albo z 0.7.2 lub starszej na Macu: pobierz 0.7.7 i zainstaluj ją na obecnej wersji. Twoje czaty i ustawienia są przechowywane w twoim profilu, poza aplikacją.

Gdy „Samodzielne łączenie z internetem” jest włączone, VOOL sprawdza dostępność nowej wersji przy uruchomieniu
i raz dziennie, a powiadomienie pokazuje bez naciskania „Sprawdź aktualizacje”. Gdy jest wyłączone, VOOL niczego
nie pobiera, a nagłówek pokazuje **Sprawdzanie aktualizacji wyłączone**.

Podpisany pakiet aktualizacji przechodzi przez weryfikowany proces z automatycznym wycofaniem:

1. **Pobieranie** — weryfikowane względem podpisanego manifestu.
2. **Instalacja przygotowawcza** — nowa wersja jest instalowana obok starej.
3. **Migracja** — twoje dane są przenoszone do nowej wersji magazynu. Jeśli migracja się nie powiedzie,
   dane zostają przywrócone do poprzedniej wersji, a aplikacja uruchamia się ponownie w tej wersji: *„Your data was
   restored to the previous version.”*
4. **Kontrola stanu** — nowa wersja musi wykazać, że potrafi obsługiwać żądania, zanim stara zostanie wycofana.
5. **Zamiana** — atomowa; poprzednia wersja jest zachowywana jako cel wycofania.

W Linuksie instalacja z archiwum tar aktualizuje się w ten sam sposób. Nowa wersja jest instalowana obok
działającej, a `current` przełącza się na nią w jednym kroku. Jeśli nowa wersja nie działa poprawnie, VOOL sam
wraca do poprzedniej. Ręczny przycisk **Wróć do …** jest dostępny na Macu i przy instalacjach ze źródeł, ale
jeszcze nie przy instalacji z archiwum tar w Linuksie.

Jeśli którykolwiek etap się nie powiedzie, aplikacja pozostaje taka, jaka była — komunikat o niepowodzeniu
podaje etap (`verification_failed`, `insufficient_disk_space`, …), a każdy etap ma własne zdanie mówiące,
jak przywrócić działanie.

## Zgodność danych

* Twój profil, czaty, potwierdzenia i ustawienia są przechowywane poza pakietem aplikacji, więc zastąpienie
  aplikacji nigdy nie zastępuje twoich danych.
* Lokalny magazyn odmawia otwarcia po **obniżeniu wersji**: baza danych zapisana przez nowszy VOOL nie zostanie
  po cichu odczytana przez starszy — starszy to komunikuje i odmawia, ponieważ zgadywanie nieznanego układu
  magazynu by go uszkodziło. Aby odczytać nowe dane, zainstaluj ponownie nowszą wersję.
* Pliki zainstalowane przez aktualizację i sam stan aktualizacji są zapisywane w twoim profilu wraz
  z potwierdzeniami, więc na pytanie „która wersja zmieniała moje dane” można odpowiedzieć po fakcie.

## Kanał beta

Obecna linia wydań to **beta** (zobacz [Stan wydań](../trust/release-status.md)).
Aktualizacje są podpisywane przypiętym kluczem wydawcy i sprawdzane przed instalacją. Sama aplikacja na macOS
jest podpisana ad hoc i nie jest poświadczona notarialnie, więc macOS może poprosić cię o potwierdzenie pierwszego
uruchomienia nowej wersji.

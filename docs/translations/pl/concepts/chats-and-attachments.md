---
description: Czaty, prywatne czaty oraz pliki i długie wklejenia, które do nich dodajesz: limity, co jest zachowywane i co może zostać odrzucone.
translation: machine
source_sha256: 1497140fef69088f8e0210aeb8a7f6abba6ae82d5132841ce397fe60fa1bab99
---

# Czaty i załączniki

Czat to jedna rozmowa z VOOL. Czaty są przechowywane na twoim komputerze. Czat może być powiązany z
folderem projektu, dzięki czemu jego narzędzia działają w tym folderze (zobacz [Obszary robocze](workspaces.md)),
a pliki możesz dodawać do niego jako załączniki.

## Załączanie plików

Użyj przycisku **Załącz** obok pola wiadomości albo przeciągnij na nie pliki. Każdy plik pojawia się jako kafelek
i trafia do następnej wysłanej wiadomości. Wiersz pod polem pokazuje limity:

* Do 8 plików na wiadomość, 10 MB na plik i 25 MB na wiadomość.
* Pliki tekstowe i pliki z kodem oraz obrazy PNG, JPEG, GIF i WebP. PDF, Word (DOCX), archiwa takie jak
  ZIP oraz wideo dochodzą do nich, gdy ten komputer ma to, czego potrzeba do ich odczytania. Okno wyboru plików
  oferuje tylko to, co ten komputer potrafi odczytać.

To, co dostaje model, też jest ograniczone: do 120 000 znaków z jednego pliku i 240 000 ze wszystkich
plików w wiadomości. Dłuższy plik jest ucinany na końcu wiersza, a zapis podaje, w którym miejscu. Jeśli
używany model nie potrafi czytać obrazów, kafelek o tym informuje, a obraz nie jest do niego wysyłany.

Zanim obraz zostanie zapisany, usuwane są z niego ukryte informacje: dane aparatu i lokalizacji (EXIF,
XMP, IPTC) oraz tekst przechowywany wewnątrz plików PNG i WebP.

Wybrany plik jest używany tylko do tej jednej wiadomości. Gdy odpowiedź jest gotowa, zawartość pliku jest
usuwana, a zachowywane jest jego potwierdzenie.

## Długie wklejenia

Wklej 4 000 znaków lub więcej, a VOOL zachowa to jako dokument czatu zamiast wiadomości, dokładnie
w takiej postaci, w jakiej zostało wklejone. **Podgląd** pokazuje jego początek. Dokument pozostaje w czacie,
dopóki nie usuniesz jego albo czatu. Kolejne pytania w tym samym czacie mogą z niego korzystać: VOOL przekazuje
modelowi do 60 000 znaków wcześniejszych dokumentów czatu, od najnowszych, i podaje, w którym miejscu
uciął.

## Gdy plik zostanie odrzucony

Odrzucony plik nigdy nie znika bez słowa. Jego kafelek zostaje i podaje powód, a wiadomości
nie można wysłać, dopóki nie usuniesz pliku albo nie spróbujesz ponownie: *Przed wysłaniem usuń nieudany
załącznik lub spróbuj ponownie.* Najczęstsze powody:

* **Za duży plik albo zbyt wiele plików.** Usuń część z nich albo wyślij je w dwóch wiadomościach.
* **Typ, którego VOOL nie odczytuje, albo plik nie jest tym, na co wskazuje nazwa.** Plik o nazwie `.txt`, który w rzeczywistości
  jest obrazem lub programem, albo PDF, dla którego ten komputer nie ma czytnika, jest odrzucany wraz z podaniem powodu.
* **Poświadczenie w środku.** Plik lub wklejony tekst, który wygląda, jakby zawierał aktywny klucz lub token, na przykład
  blok klucza prywatnego albo token dostępu, jest odrzucany i nic nie zostaje zapisane. Usuń sekret,
  a potem załącz lub wklej ponownie.

Te odmowy mają własne krótkie kody, pokazywane razem z komunikatem. Nie ma ich w
[Księdze błędów](../ERROR_BOOK.md), która obejmuje pozostałe błędy aplikacji.

## Prywatne czaty

Powiedz „prywatny czat”, „nie zapisuj tego” albo „tylko między nami”, a czat przestanie zapisywać od
tej wiadomości: ani wiadomości, ani streszczenia, ani pamięci. Baner pokazuje *Prywatny czat: nic nie jest
zapisywane*, a przy każdej prywatnej wiadomości widać małego ducha. Prywatne wiadomości nie
wracają po przeładowaniu, a VOOL nie może ich później przywołać. Powiedz „włącz ponownie zapisywanie”
albo naciśnij **Włącz ponownie zapisywanie**, aby czat znów był zapisywany od następnej wiadomości. Prywatny czat
pozostaje prywatny także po ponownym uruchomieniu VOOL.

## Zabieranie czatu ze sobą

**Eksportuj** w nagłówku czatu zapisuje cały czat jako tekst lub Markdown, z każdą wiadomością, a załączone
pliki wymienia z nazwy, nigdy z zawartością. Aby przenieść czat na inny komputer
albo zachować podpisaną kopię, zobacz [Kopia zapasowa i odzyskiwanie](../help/backup-and-recovery.md).

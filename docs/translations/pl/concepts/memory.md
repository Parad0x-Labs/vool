---
description: Jak VOOL zapamiętuje informacje między sesjami i gdzie przechowywana jest ta pamięć.
translation: machine
source_sha256: 65af63e755076fe33675d7a5d6afa7f0c5664b77f716ffc0dc1f0f358e67f667
---

# Pamięć

Pamięć to sposób, w jaki VOOL przenosi fakty między sesjami. Jest przechowywana jako pliki na twoim
komputerze, w folderze danych VOOL, w czytelnym formacie.

## Jak dobrze zapamiętuje

Na 150 nigdy wcześniej nieużywanych pytaniach z testu porównawczego długich rozmów LoCoMo pamięć dostarczana w
VOOL 0.7 odpowiedziała poprawnie na 123 (82,0%). W rundzie bezpośredniego porównania na tych samych pytaniach, przy tym samym
modelu odpowiadającym za wszystkie trzy, VOOL uzyskał 124 (82,7%), OpenClaw 107 (71,3%), a Hermes 94 (62,7%),
wysyłając przy tym od 3,5 do 5,4 razy mniej tokenów na pytanie. Każdy przebieg jest opublikowany:
[testy porównawcze pamięci](https://github.com/Parad0x-Labs/vool/tree/main/docs/benchmarks).

## Właściwości

* **Na dysku, jawnie.** Pamięć to pliki, które możesz przeczytać. `MEMORY.md` to jej czytelna
  postać; fakty, które za nią stoją, są przechowywane w pliku wpisów obok niego.
* **Lokalnie.** Pamięć nie jest synchronizowana z żadnym serwerem.
* **Jeden magazyn na folder domowy VOOL.** Pamięć nie jest dzielona według obszarów roboczych — jest przechowywana
  dla całej instalacji, więc nie stanowi granicy izolacji między projektami.

## Zarządzanie pamięcią

Notatki, które sam dodasz do `MEMORY.md`, są zachowywane. Fakty zapisane przez VOOL usuwa się
za pomocą operacji zapominania VOOL, a nie przez usunięcie pliku: zapominanie w jednym kroku przepisuje
magazyn wpisów i jego lustrzaną kopię, a potem ponownie odczytuje oba, aby potwierdzić, że tekstu
już nie ma. Samo usunięcie `MEMORY.md` nie usuwa danych — plik wpisów nadal przechowuje
fakty, a lustrzana kopia jest z niego odtwarzana.

W trybie chmury odpowiednia część pamięci może trafić do kontekstu wysyłanego do dostawcy, na
tych samych zasadach co każdy inny kontekst. Zobacz [Lokalnie i w chmurze](local-and-cloud.md).

Zapomnienie w czacie usuwa też te słowa z historii przebiegów tego czatu w bazie danych
środowiska uruchomieniowego: z zapisów tur, punktów kontrolnych, prób, planów zadań, sfinalizowanych odpowiedzi (wraz z
kopiami, które obejmuje usuwanie odpowiedzi), kandydatów na wiedzę, raportów kontekstu, powiadomień,
artefaktów badawczych utworzonych przez czat oraz przypiętych wiadomości, które je cytowały. Zapisy, od których
zależą inne zapisy, pozostają, ale zapomniane słowa są w nich wyczyszczone, razem z każdym skrótem
przechowywanym obok nich; pozostałe zapisy są usuwane. Tura, która jeszcze trwała w chwili zapomnienia,
nie może z powrotem zapisać tych słów: jej zdarzenia są czyszczone, a gdy się zakończy, jej zapisy
są usuwane ponownie. Następnie dziennik zapisu z wyprzedzeniem bazy danych jest scalany, aby starsze kopie nie zostały
w pliku `-wal`. Zapisy innego czatu pozostają nietknięte. Wiadomość w kolejce, która nie
została jeszcze wysłana, jest zachowywana, a zapomnienie informuje wtedy, że nie dało się go w pełni zastosować. Tabela,
której nie zna żaden krok usuwania, jest wymieniana w odpowiedzi, która mówi, że ponowna próba jej nie wyczyści.

Słowa są porównywane bez względu na wielkość liter, a cyfra zapisana w innym systemie pisma jest
tą samą cyfrą (pełnoszerokie „１２” to 12). Zapomniana liczba pasuje tylko tam, gdzie występuje
jako samodzielna liczba: litery i cyfry zapisane razem z nią nie mogą zawierać żadnej innej
cyfry i nie może ona przechodzić przez `.`, `,`, `:`, `/`, `-` ani `_` w kolejną cyfrę. Zapomnienie „12” usuwa „kod 12”, „12kg” i „B12”,
ale nie „pokój 312”, „3.12”, „12.5”, „12:30” ani „2026-10-12”, a zapomnienie „200” nie
usuwa „1,200”. W historii przebiegów sama liczba, którą środowisko uruchomieniowe policzyło lub zmierzyło (na przykład
wartość `elapsed_ms` lub `step_count`), nie jest dopasowywana.

Czytniki pamięci sprawdzają też trwały, rozstrzygający rejestr zapomnień. Nowy folder pamięci tworzy go,
zanim zacznie przyjmować zapisy. Starszy magazyn semantyczny jest uaktualniany na miejscu tylko wtedy, gdy
nie odnotowano w nim wcześniejszego usuwania; zachowane w nim wpisy pamięci pozostają dostępne. Istniejący,
ale nieczytelny rejestr, brak rejestru przy zachowanym świadku albo zapisana bariera zapomnienia
sprawiają, że czytniki wstrzymują dotkniętą pamięć. Usunięcie plików rejestru nie jest sposobem
na przywrócenie zapomnianej treści.

Oczekujący dziennik usuwania zachowuje swój token na potrzeby odzyskiwania. Gdy istnieje już trwałe
unieważnienie o określonym zakresie, zakończenie operacji czyści zbędny token dziennika, zachowując
identyfikator i status operacji. Rejestr unieważnień pozostaje.

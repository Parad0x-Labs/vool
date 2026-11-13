---
description: Języki, dostępność, historia aktywności, potwierdzenia i sposób weryfikowania odpowiedzi modeli.
translation: machine
source_sha256: 519707395e736b24ba96005597cca3bc59dcf12ca85d905c70f8a50884c5bba2
---

# Języki, dostępność i zapisy

## Języki interfejsu

Interfejs VOOL jest dostępny w wielu językach; nowsze teksty mogą jeszcze wyświetlać się po angielsku,
dopóki nie pojawi się tłumaczenie. Wybór języka dotyczy wyłącznie sposobu prezentacji: decyzje
o uprawnieniach, upoważnienie do wydatków i kody błędów są **z założenia niezależne od języka** — przetłumaczony
komunikat o błędzie nigdy nie może poszerzyć ani zawęzić tego, o czym przesądziła rozstrzygająca wersja angielska.

## Dostępność

* Skróty klawiszowe dla głównych czynności (zobacz [Skróty klawiszowe](shortcuts.md)).
* Jasny i ciemny wygląd dokumentacji i aplikacji, zgodny z ustawieniem systemu, z możliwością ręcznej
  zmiany.
* Błędy są podawane tekstem, a nie samym kolorem; działania nieodwracalne zawsze wymagają wyraźnego
  potwierdzenia, a nie wystarcza do nich jedno przypadkowe kliknięcie.

## Aktywność

Widok aktywności to pełna historia tego, co zrobił VOOL: wywołania narzędzi z ich danymi wejściowymi
i wynikami, udzielone i odmówione przez ciebie zgody oraz płatności wraz z ich potwierdzeniami. Tura
zatrzymana przez zabezpieczenie pokazuje zatrzymanie i to zabezpieczenie — odmowa jest zapisanym zdarzeniem,
a nie brakiem wpisu.

## Potwierdzenia

Działania związane z pieniędzmi i wydatkami zapisują **potwierdzenia**: co zostało zaproponowane, co
zatwierdzone, co wysłane i co rozliczone. Nieznane wyniki są zapisywane jako nieznane — nigdy nie są
przedstawiane jako sukces ani jako niepowodzenie. Dzięki temu polecenie *„Przed ponowną próbą sprawdź jej status”*
da się wykonać: potwierdzenie mówi, w jakim stanie płatność faktycznie jest.

## Stan weryfikacji modeli

VOOL odróżnia to, co model **powiedział**, od tego, co środowisko uruchomieniowe **udowodniło**:

* Wyniki narzędzi są zapisywane wraz z zapisami ich wykonania; twierdzenia odpowiedzi dotyczące plików,
  płatności lub danych są powiązane z tymi zapisami, a twierdzenia bez pokrycia są wstrzymywane.
* Potwierdzenia mają status weryfikacji; tam, gdzie dana ścieżka jest w tej kompilacji eksperymentalna lub
  nie da się jej zweryfikować, status mówi to wprost, zamiast sugerować dowód.

Pojęcie opisuje strona [Potwierdzenia](../concepts/receipts.md), a sposób klasyfikowania niepowodzeń — strona
[Błędy](errors.md).

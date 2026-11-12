---
description: Pięć trybów, na co każdy z nich pozwala VOOL bez pytania i co obejmuje każdy przycisk zgody.
translation: machine
source_sha256: 1f33c8d63bcdeddbd1ac44d3b2d948b354137954ee6accb6ee18eeec8bdda1c6
---

# Uprawnienia

VOOL decyduje, co może się uruchomić, zanim cokolwiek się uruchomi. Model może poprosić o czynność; o tym, czy
do niej dojdzie, decydują wybrany przez ciebie tryb i twoje odpowiedzi. Odczyt jest traktowany inaczej niż
zmiana, a sekrety, płatności i ustawienia bezpieczeństwa są chronione w każdym trybie.

## Tryby

Tryb wybierasz w menu obok pola wiadomości. Nowy czat zaczyna w trybie **Ręczny**.

| Tryb | Działa bez pytania | Najpierw pyta | Nigdy |
| --- | --- | --- | --- |
| Ręczny | Odczyt i wyświetlanie listy plików, bezpieczne polecenia oraz publiczne wyszukiwania w sieci tylko do odczytu | Każda zmiana: edycje plików, polecenia, działania sieciowe, ustawienia, Git, wdrażanie i wiadomości | Odczyt zapisanych sekretów |
| Przegląd edycji | To samo co Ręczny | To samo co Ręczny. Każda partia edycji jest pokazywana jako zmiana do przejrzenia, zanim zostanie zastosowana | Odczyt zapisanych sekretów |
| Plan | Odczyt i wyświetlanie listy plików oraz bezpieczne polecenia | Nic: tylko odczytuje i proponuje plan | Każda zmiana, każde działanie sieciowe |
| Auto | Odczyt, tworzenie i edycja plików, polecenia, działania w sieci i w przeglądarce oraz dostawcy modeli | Usuwanie, nadpisywanie istniejących plików, instalowanie zależności, zmiana ustawień dostawców, commit, push, merge i reset w Git, wdrażanie, wysyłanie wiadomości, płatności i zmiana ustawień | Odczyt zapisanych sekretów, zmiana ustawień bezpieczeństwa i każda czynność, której skutku VOOL nie potrafi określić |
| Ominięcie uprawnień | Wszystko poza czterema chronionymi klasami | Płatności | Odczyt zapisanych sekretów, zmiana ustawień bezpieczeństwa i każda czynność, której skutku VOOL nie potrafi określić |

Tryb, umiejętność ani wtyczka mogą jedynie zawęzić to, co jest oferowane. Żadne z nich nie przyznaje czynności,
na którą tryb nie pozwala.

## Odpowiadanie na prośbę o zgodę

Gdy czynność wymaga twojej decyzji, nad polem wiadomości otwiera się pasek **Wymagane uprawnienie**. Podaje on
dokładną czynność: wiersz polecenia, który zostanie uruchomiony, albo pliki, które się zmienią, informację, czy
zmianę można cofnąć, oraz podgląd edycji. Nic nie zostanie uruchomione, dopóki nie odpowiesz, a prośba
czeka na ciebie nawet po ponownym uruchomieniu VOOL. Prośba, której czas minął, jest pokazywana jako wygasła
i nie można jej zatwierdzić.

Pasek oferuje tylko te wybory, które pasują do czynności:

| Przycisk | Co dopuszcza |
| --- | --- |
| Zezwól raz | Tę jedną, dokładnie określoną czynność. |
| Zezwól dla tego zadania | Tę samą, dokładnie określoną czynność ponownie w trakcie tego zadania, w tym samym trybie. Oferowany dla czynności, które nie są edycjami plików. |
| Zezwól na wszystkie *N* planowane zmiany dla tego żądania | Zmiany plików, które to żądanie już zaplanowało, dokładnie te pliki wymienione w „Przejrzyj szczegóły”, i nic więcej. |
| Zezwól na edycje w przestrzeni roboczej w tym czacie | Odczyty, nowe pliki i edycje wewnątrz obszaru roboczego tego czatu, przez osiem godzin albo do ponownego uruchomienia VOOL lub usunięcia czatu. |
| Zezwól w tym projekcie | Odczyty i zapisy plików wewnątrz folderu projektu. Obowiązuje dla projektu, także po ponownym uruchomieniu i w nowych czatach. |
| Przejrzyj szczegóły | Pokazuje pełną prośbę, zanim zdecydujesz. |
| Odmów | Czynność nie zostaje wykonana. |

Niezależnie od tego, na co zezwolisz, usuwanie, przenoszenie, nadpisywanie, polecenia, działania sieciowe, sekrety
i płatności nadal za każdym razem pytają osobno, chyba że pozwala na nie sam tryb.

## Ominięcie uprawnień

Ominięcie uruchamia dostępne narzędzia bez osobnych pytań, w zakresie i na czas, które
wybierzesz:

* **Zakres:** tylko bieżące zadanie, ta sesja czatu albo ten projekt.
* **Czas trwania:** 15 lub 30 minut, 1, 2, 4 lub 8 godzin, własny czas od 15 do 1440 minut
  albo *Aż to wyłączę*, tylko dla tego czatu.

Ominięcie na określony czas działa w każdym czacie. *Aż to wyłączę* wymaga, aby czat był powiązany z folderem
projektu, a tym folderem nie może być cały twój folder domowy, katalog główny dysku ani folder, który
zawiera własne dane VOOL. Gdy ominięcie jest włączone, baner pokazuje jego zakres i czas zakończenia, a jego
**×** natychmiast je wyłącza; czat wraca wtedy do trybu Ręczny. Usunięcie czatu również kończy jego ominięcie.
Ominięcie nigdy nie daje dostępu poza obszarem roboczym i nie może pozwolić modelowi rozszerzyć własnych
uprawnień. Płatności nadal pytają, a sekrety i ustawienia bezpieczeństwa pozostają poza zasięgiem.

## Płatności i wydatki

Zatwierdzenie czynności nigdy nie zatwierdza płatności. Płatności przechodzą przez własną propozycję i
zatwierdzenie portfela, z jego limitami, w każdym trybie. Płatne wywołania modeli są wstrzymywane przez twoje
limity wydatków i zabezpieczenia cenowe, zanim nastąpi kontakt z dostawcą. Zobacz [Portfel](../guides/wallet.md) i
[Limity wydatków i zabezpieczenia cenowe](../guides/spending-limits.md).

## Gdy czynność zostanie odrzucona

Odrzucona czynność niczego nie zmieniła, a komunikat mówi, co zostało odrzucone i dlaczego. Kody,
które możesz zobaczyć, to [`permission_denied`](../ERROR_BOOK.md#permission-denied),
[`confinement_refusal`](../ERROR_BOOK.md#confinement-refusal) dla zmiany poza dozwolonym
folderem oraz [`tool_unavailable`](../ERROR_BOOK.md#tool-unavailable) dla narzędzia, którego ten tryb nie
oferuje. Zobacz [Błędy i kody błędów](../reference/errors.md).

---
description: Kontakty — zapisane osoby i punkty końcowe, z przeglądem zmian, który chroni każdą edycję.
translation: machine
source_sha256: 8be5d771229a4d6c4b439af014f0407649ed694fd26f9fd880009d25f5b8a347
---

# Kontakty

Lista kontaktów to lokalna, uporządkowana książka adresowa (osoby, ich punkty końcowe i aliasy
płatności), z której VOOL może korzystać, zamiast prosić cię o ponowne wpisanie adresu. Ponieważ książka
adresowa zasila płatności i wiadomości, edycje w niej są **chronione**: przeglądane, zatwierdzane i
zapisywane.

## Chronione edycje

Zmiana zapisanego kontaktu to proces dwuetapowy:

1. **Przegląd** — zmiana jest przygotowywana i pokazywana. Jeśli wpis zmienił się od chwili jego otwarcia
   (w innym oknie, na innym urządzeniu), zmiana jest odrzucana z komunikatem *„changed after this was
   reviewed; review it again. Nothing changed.”*
2. **Zatwierdzenie** — zmiana zostaje wprowadzona tylko po podaniu twojego PIN-u lub hasła na ekranie przeglądu.

Każda odmowa jasno stwierdza, że **nic nie zostało zapisane ani zmienione** — bo nic nie zostało. Jeśli
twój PIN został przyjęty, ale samo zapisanie się nie powiodło, komunikat mówi dokładnie to: PIN został przyjęty,
zapisz ponownie, zanim zatwierdzenie wygaśnie.

## Zabezpieczenia

* Jedna zmiana na kontakt naraz; ograniczona liczba oczekujących zmian.
* Usunięcie kontaktu nie przepisuje po cichu historii: odwołania do usuniętego kontaktu są
  zgłaszane jako *„That contact was deleted”* — nigdy nie są po cichu przypisywane na nowo.
* Importy nigdy nie są stosowane częściowo: jeśli źródła nie dało się odczytać, nic nie zostało zaimportowane.

## Dlaczego tak to zaprojektowano

Literówka w adresie płatności to pieniądze nie do odzyskania. Krok przeglądu istnieje po to, aby żadna tura
modelu, żadna zbiorcza edycja i żadne nieaktualne okno nie mogły zmienić tego, na kogo wskazuje „Alice”, bez
potwierdzenia przez człowieka dokładnego wynikowego wpisu.

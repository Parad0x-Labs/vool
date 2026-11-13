---
translation: machine
source_sha256: 4596ad3e82d6936445beafa8e30a334325d70a8a0f1ee052eda3c43ae5118244
---
# Kontrole dodatków

Dodatki oddzielają od siebie przeglądanie, sprawdzanie i instalację. Skan opisuje konkretne bajty;
nie jest gwarancją bezpieczeństwa, poparciem wydawcy, uprawnieniem w czasie działania ani zatwierdzeniem w portfelu.

Przeglądaj pokazuje najpierw własne umiejętności VOOL, w tym 75 umiejętności VOOL dołączonych do aplikacji (22 z nich
korzysta z narzędzi VOOL, które jedynie zawężają to, co VOOL już oferuje), a potem przejrzane dodatki z katalogu. Kodeks umiejętności pokazuje najpierw umiejętności zespołu VOOL, nad wyborem redakcji
i polecanymi umiejętnościami.

## Umiejętności stworzone przez VOOL: kontrola lokalna

75 umiejętności stworzonych przez VOOL jest dostarczanych wewnątrz aplikacji; każda jest przypięta przez SHA-256 we własnym katalogu VOOL i
sprawdzana przy wydaniu VOOL. Dla nich **Dodaj tę umiejętność** uruchamia tylko lokalną kontrolę VOOL: bez klucza,
bez limitu skanów i bez żądań sieciowych.

1. VOOL odczytuje umiejętność i licencję z własnej kopii aplikacji i odrzuca je, jeśli ich SHA-256 nie
   zgadza się z przypiętym (*This VOOL skill's file doesn't match the copy that came with VOOL, so it was not added.*).
2. Lokalna kontrola VOOL czyta umiejętność pod kątem ryzykownych instrukcji i pokazuje wynik. Czysty wynik
   można dodać przyciskiem **Dodaj i włącz**; wszystko, co oznaczy, wymaga twojej osobnej, wyraźnej zgody na
   tę konkretną wersję. **Odrzuć** ją porzuca.
3. Potem instalacja przebiega tym samym cyklem życia co w przypadku innych umiejętności: dokładne pliki są instalowane,
   weryfikowane i włączane. Zainstalowana umiejętność jest sprawdzana względem przypiętego SHA-256 przy każdym
   ładowaniu; zmieniony plik czyni ją niedostępną.

Kontrola lokalna to statyczny odczyt ustalonych bajtów, a nie gwarancja bezpieczeństwa ani jakości odpowiedzi.
Umiejętności od innych twórców nigdy nie idą tą ścieżką.

## Publiczne umiejętności

Wybierz publiczną umiejętność z GitHuba w ustalonej rewizji i zatwierdź pobranie jej zawartości. VOOL sprawdza
ją na tym urządzeniu za pomocą `core/local_scan.py`. Bez włączonej wtyczki skanera strona wyjaśnia,
że kontrole pozostały na tym urządzeniu. Ta ścieżka nie wymaga klucza skanera.

Włączona wtyczka skanera otrzymuje także zawartość publicznej umiejętności, gdy zażądano skanu. Może to
zużywać limit jej usługi. Lokalne czaty, prywatne pliki obszaru roboczego i inne dane uwierzytelniające
nie są przesyłane. Zapisanie klucza i otwarcie Dodatków nie wykonuje testu połączenia ani żądania skanu.
Nie ma automatycznych ponowień żądań.

Wyniki czytasz, a instalację akceptujesz osobno. Walidacja raportu wymaga pełnego pokrycia
i prawidłowych wyników. Raport z wynikiem pozytywnym nie ma ustaleń ani naruszeń zasad. Osobno oferowana
akceptacja ryzyka wiąże przejrzaną wersję; nigdy nie zmienia raportu ani nie daje dodatkowego dostępu.
Wygasłe przeglądy, zmienione bajty w przygotowanym pobraniu, dowiązania i kolidujące foldery docelowe są odrzucane.
Pokwitowania wiążą całą zainstalowaną dystrybucję. Zmienione pliki odbierają dostępność w czasie działania.
Wskazówki umiejętności są wstawiane w całości albo wyraźnie pomijane, gdy nie mieszczą się w budżecie promptu.

## Opcjonalne skanery

Zdalny skaner jest opcjonalny. Gdy żadna wtyczka skanera nie jest włączona, o tym, czy umiejętność można zainstalować,
decyduje własna kontrola VOOL na tym urządzeniu; wcześniejsze wersje wymagały zamiast tego zdalnego skanu.

Aby go dodać, zainstaluj wtyczkę skanera od jej twórcy w sekcji Wtyczki, zapisz własny
klucz, przejrzyj dostęp i ją włącz. Jej deklaracja podaje ścieżkę do wyraźnego testu połączenia
i ścieżkę do zleconego skanowania, a te żądania wykonuje wspólny natywny transport wtyczek.
VOOL nie ma wbudowanego klienta skanera ani centralnego ustawienia klucza skanera i nie oferuje własnej
wtyczki skanera. Klucz Eyebrow zapisany w starszym VOOL pozostaje na tym komputerze i nie jest używany.
Identyfikatory `vool-plugin-eyebrow` i `vool-plugin-usepod` są zarezerwowane; inny pakiet, który rości sobie
jeden z nich, jest odrzucany przed przeglądem.

Własna kontrola VOOL na tym urządzeniu działa zawsze. Raport wtyczki skanera jest dodawany obok niej:
jeśli którakolwiek z nich znajdzie problem, instalacja jest blokowana, a raport wtyczki musi wskazywać dokładną
sprawdzoną zawartość. Wtyczka skanera nigdy nie może sama zatwierdzić instalacji.

Włączony skaner z brakującym lub odrzuconym kluczem kończy się jawnym błędem. VOOL nigdy po cichu nie zastępuje
zleconego zdalnego skanu pozytywnym wynikiem lokalnym. Przekroczenia czasu, błędy usługi, przekierowania,
zbyt duże odpowiedzi i niepełne raporty pozostawiają instalację niezaakceptowaną. Żądanie, które przekroczyło czas,
mogło mimo to zużyć limit usługi.

## Pakiety wykonywalne i przegląd sklepu

Wtyczki przechodzą pełny przegląd pakietu z folderu lokalnego lub przypiętego publicznego pakietu z GitHuba, oddzielny od
importu umiejętności tylko tekstowych. Instalacja nie uruchamia kodu pakietu i pozostawia go wyłączonym. Wtyczki ze sklepu
wymagają też istniejącego podpisanego pokwitowania i pełnego skrótu dystrybucji. Włączenie wymaga
osobnego arkusza dostępu obejmującego narzędzia, skutki, dokładne hosty, gniazda danych uwierzytelniających i dołączone serwery.

Kod wykonywalny przy wywołaniu otrzymuje tylko własne zadeklarowane klucze. Nie może odczytywać gniazd innej
wtyczki, płatnych kluczy modeli właściciela ani uprawnień portfela do podpisywania, limitów, zatwierdzeń czy pokwitowań.
Jego wynik pozostaje niezaufany. Zmienione bajty cofają zgodę; wyłączenie zatrzymuje wywołania. Nieobsługiwane
odizolowanie odmawia wykonania. Limity środowiska uruchomieniowego opisuje [Tworzenie wtyczek](../../../guides/plugin-authoring.md).

Jeśli VOOL nie może odczytać swoich zapisów o wtyczkach, pakiety, dla których nie ma zapisu, przestają oferować umiejętności, z wyjątkiem
tych dostarczanych z VOOL, a uszkodzony plik jest zachowywany obok nowego. Jeśli nie może odczytać
twoich wyborów włączenia i wyłączenia, każda wtyczka liczy się jako wyłączona, dopóki znów jej nie włączysz.

## Lokalne API i transport

`GET /api/addons` odczytuje spis. Zmiany przez `POST /api/addons` wymagają lokalnego dostępu właściciela,
JSON, żądań przeglądarki z tego samego źródła i pól właściwych dla danej akcji. Punkty włączania/wyłączania i cyklu życia wtyczek
(`POST /api/plugins/enable`, `POST /api/plugins/lifecycle`) stosują tę samą zasadę JSON i
tego samego źródła. Pobieranie publicznych źródeł używa
dokładnych rewizji i zweryfikowanych blobów o ograniczonym rozmiarze. Usługi natywne wymagają zadeklarowanych publicznych hostów HTTPS,
weryfikują TLS, przypinają rozwiązane adresy publiczne, odrzucają przekierowania, ograniczają odpowiedzi i zatrzymują się, gdy
wtyczka jest wyłączona.

Lokalne udawane usługi HTTPS pozwalają zweryfikować transport i obsługę błędów bez płatnych wywołań. Nie
potwierdzają niezawodności prawdziwej usługi, rozliczeń, kompetencji modeli ani instalacji wewnątrz innych
aplikacji. To wymaga osobnej weryfikacji.

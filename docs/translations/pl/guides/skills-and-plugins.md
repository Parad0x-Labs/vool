---
description: Rozszerzanie VOOL o umiejętności i wtyczki.
translation: machine
source_sha256: 4dd2a317a7b10ee024616f6e0c27bd73087c44334f29e2c5f60f69773f4e54e8
---

# Umiejętności i wtyczki

**Umiejętność** to spakowany zestaw instrukcji do powtarzalnego zadania. **Wtyczka** dodaje narzędzia,
dostawcę modeli albo skaner dodatków. Wtyczki mogą też zawierać umiejętności.

## Wbudowane a instalowane z zewnątrz

VOOL ma **natywną bibliotekę umiejętności** — umiejętności dostarczane razem z samą aplikacją — oraz,
w obecnych kompilacjach, wtyczkę **VOOL Database**. Możesz też instalować własne umiejętności i wtyczki.

| | Wbudowane (dostarczane z VOOL) | Instalowane z zewnątrz |
| --- | --- | --- |
| Gdzie się znajdują | Wewnątrz aplikacji | Twój folder wtyczek (zarządzany z okna wtyczek w VOOL, a nie z Ustawień) |
| Aktualizacje | Razem z aplikacją | Gdy zaktualizujesz pakiet |
| Można wyłączyć | W oknach aplikacji nie ma jeszcze takiego przycisku | Tak — z okna wtyczek |
| Przyznają uprawnienia | **Nie** | **Nie** |

Dla obu źródeł obowiązują dwie zasady:

* **Umiejętności uczą; nigdy niczego nie przyznają.** Narzędzia zadeklarowane przez umiejętność są sygnałem
  *zawężającym* (budżetem narzędzi danego przepływu pracy), nigdy przyznaniem czegokolwiek. Kontroler uprawnień
  nie odczytuje umiejętności.
* **Jeden spis.** Katalog pokazywany w konsoli i pakiety ładowane przez środowisko uruchomieniowe to
  ta sama lista. To, że umiejętność pojawia się na liście, nie dowodzi, że da się ją załadować — każdy wpis ma
  swój bieżący stan (dostępna, wyłączona, brak wymagania wstępnego, nieprawidłowy kontrakt) i powód.

## Znajdowanie umiejętności

Strona Umiejętności i wtyczki pokazuje najpierw własne umiejętności VOOL. **Przeglądaj** zawiera 75 gotowych
umiejętności VOOL. Każda z nich to zestaw instrukcji dołączony do aplikacji, więc Przeglądaj wyświetla je
bez wyszukiwania online. 22 z nich korzysta z narzędzi VOOL (twojej poczty, notatek, plików, PDF-ów, portfela i
innych) w turach, w których zostaną wybrane: narzędzia umiejętności jedynie zawężają to, co VOOL już oferuje dla
twojej prośby, wyłączone narzędzie pozostaje wyłączone, a każde wywołanie pyta jak zawsze. Jej
**Źródło i szczegóły techniczne** wymieniają narzędzia, z których korzysta. Karta **Kodeks umiejętności** pokazuje najpierw umiejętności zespołu
VOOL. Przejrzyj umiejętność, zanim ją włączysz.

Sposób dodania umiejętności z Przeglądaj zależy od tego, kto ją stworzył:

* **Umiejętności stworzone przez VOOL** (wspomniane wyżej 75): naciśnij **Dodaj tę umiejętność**. VOOL sprawdza na twoim
  komputerze, czy plik jest dokładnie tym, z którym został dostarczony, i czyta go pod kątem ryzykownych
  instrukcji, a potem pokazuje ci wynik. Klucz nie jest potrzebny i nic nie jest nigdzie wysyłane.
  Jeśli plik się nie zgadza, VOOL go odrzuca i o tym informuje.
* **Umiejętności od innych twórców** (innych wydawców albo z wklejonego przez ciebie linku do GitHuba): naciśnij
  **Pobierz i przeskanuj**. VOOL pobiera publiczną umiejętność w ustalonej wersji i sprawdza ją na
  twoim komputerze. Jeśli włączono wtyczkę skanera, ona również otrzymuje zawartość umiejętności, a
  jej raport jest dodawany obok raportu VOOL. Przeczytaj wynik, a potem zdecyduj, czy dodać
  umiejętność: jeśli którakolwiek kontrola zgłosi problem, VOOL doda ją dopiero, gdy zaakceptujesz ryzyko dla
  tej wersji. Klucz nie jest potrzebny, chyba że twoja wtyczka skanera o niego prosi.

Możesz też stworzyć własną umiejętność na czacie zupełnie bez klucza: zobacz
[Stwórz własną umiejętność](../../../guides/make-your-own-skill.md).

Możesz też zapytać na dowolnym czacie: *jakie mam umiejętności?* wyświetli ich listę, a *jak dodać
umiejętność?* wyjaśni oba sposoby.

## Włączanie i wyłączanie umiejętności

Otwórz **Umiejętności i wtyczki**, potem **Moje dodatki**, potem **Umiejętności**. Każda umiejętność pokazuje **Włączone** lub
**Wyłączone** z przyciskiem **Włącz** lub **Wyłącz**. Wyłączona umiejętność nie jest używana na żadnym czacie,
dopóki znów jej nie włączysz. Jej pliki są zachowywane.

Karta **Built-in skills** w sekcji **Wtyczki** nie ma własnego przycisku: włączaj i wyłączaj jej umiejętności
pojedynczo w sekcji **Umiejętności**.

## Która umiejętność wpłynęła na odpowiedź

Gdy instrukcje umiejętności były częścią tego, jak VOOL napisał odpowiedź, mówi o tym krótki wiersz pod
odpowiedzią, na przykład **Użyta umiejętność: Browser**. Pojawia się zaraz po odpowiedzi, dopóki
czat jest otwarty; odpowiedzi wczytane później z historii jeszcze go nie pokazują. Zasady stylu odpowiedzi,
które towarzyszą większości odpowiedzi, nie są tam wymieniane.

## Umiejętności do budowania VOOL

Biblioteka zawiera też umiejętności, z pomocą których zespół VOOL buduje VOOL: przegląd kodu, uruchamianie testów,
kontrole wydania, naprawę potoku i podobne. Nie są wymieniane na czacie ani na stronie Umiejętności
i nie sterują codziennymi odpowiedziami. Programiści mogą je pokazać i używać, uruchamiając VOOL z
`VOOL_DEVELOPER_SKILLS=1`.

## Stany dostępności

Umiejętność może być pokazana jako niedostępna, z powodem określonego typu: brak wymagania wstępnego (na przykład
umiejętność bazy danych wymaga włączonej wtyczki bazy danych), funkcja niedostępna na tym komputerze,
nieprawidłowy kontrakt albo wyłączenie przez ciebie. Powody są pokazywane przy każdym wpisie, a wyłączenie
dotyczy pojedynczego wpisu i jest zachowywane po ponownym uruchomieniu.

## Zduplikowane tożsamości

Jeśli dwa pakiety deklarują tę samą tożsamość wtyczki (na przykład pakiet wbudowany i zainstalowana kopia),
zachowywany jest **deterministycznie** pakiet wbudowany, a odrzucona kopia jest zgłaszana w
katalogu — nigdy nie jest po cichu przesłaniana.

## Instalowanie własnych

Instaluj z katalogu w aplikacji albo wskaż VOOL własny folder wtyczek. Pakiety zawierają
manifest (`plugin.json`) deklarujący ich narzędzia; manifest, którego deklaracje uprawnień nie
pasują do zadeklarowanej klasy zachowania, jest odrzucany przy ładowaniu.

{% hint style="danger" %}
Przed weryfikacją przejrzyj cały pakiet. Przegląd jest dowodem dotyczącym konkretnych bajtów,
a nie certyfikatem bezpieczeństwa ani przyznaniem uprawnień.
{% endhint %}

## Wtyczki w Dodatkach

Otwórz **Dodatki → Moje dodatki → Wtyczki**. Przejrzyj folder lokalny albo publiczny folder z GitHuba w
dokładnym commicie. Instalacja przechwytuje cały pakiet i pozostawia go wyłączonym. Podpisane
wtyczki ze sklepu używają istniejącego łańcucha rejestru i również instalują się wyłączone. Osobno przejrzyj arkusz
dostępu, zanim włączysz narzędzia lub usługi. Zapisanie klucza usługi nie wysyła żadnego żądania;
testy połączenia i odświeżanie listy modeli to wyraźne działania.

Wyłączenie usuwa narzędzia i zatrzymuje trwające wywołania. Odinstalowanie najpierw wyłącza, usuwa własne pliki
i folder roboczy, domyślnie usuwa zapisane klucze i zachowuje historię. Aktualizacje i ponowne instalacje
wymagają nowego przeglądu i startują wyłączone. Aktualizacja z innego repozytorium lub od innego wydawcy
nie zachowuje zapisanych kluczy. UsePod i Eyebrow nie są już częścią VOOL, a VOOL
nie oferuje wtyczki dla żadnego z nich. Zapisany klucz UsePod lub Eyebrow pozostaje na tym komputerze, a VOOL
go nie używa; Dodatki informują o tym, dopóki nie zamkniesz powiadomienia. Czaty, które używały modelu UsePod,
po aktualizacji nie mają wybranego modelu, a Auto nie przełączy się w nich na model w chmurze;
powiadomienie informuje o tym, dopóki go nie zamkniesz.

Zobacz [Tworzenie wtyczek](../../../guides/plugin-authoring.md) i [Kontrole dodatków](addon-security.md).

## Pełne zatwierdzanie pakietu

Weryfikacja obejmuje każdy plik i katalog pakietu, w tym pliki wykonywalne handlerów,
dołączone biblioteki, pliki blokad zależności, pliki referencyjne, zasoby i bity trybu wykonywalnego.
Dodanie, usunięcie, zmiana nazwy lub zmiana któregokolwiek z nich unieważnia zatwierdzenie. Dowiązania
symboliczne, dowiązania twarde, pliki specjalne, tryby uprzywilejowane, niestabilne odczyty i zbyt duże dystrybucje
są odrzucane. Obecne limity przechwytywania to 4 096 wpisów, 64 MiB na plik i 128 MiB
na pakiet. Wewnątrz pakietu nie ma ignorowanych katalogów pamięci podręcznej ani stanu.

Wcześniej zapisane zatwierdzenia obejmujące wyłącznie metadane nie aktywują pakietów w tej wersji.
Przejrzyj pełną dystrybucję, ponownie ją jawnie zweryfikuj, a potem ją włącz. Aktualizacje pozostają
wyłączone, dopóki nie zostaną zweryfikowane i włączone; zatwierdzenie nigdy nie jest po cichu przenoszone.
Zarządzane dodatki wymagają też nowego, uwierzytelnionego przeglądu w sklepie, gdy starsze potwierdzenie
nie obejmuje kompletnej dystrybucji; zwykła weryfikacja nie może zastąpić tego potwierdzenia.

`requires` wymienia zainstalowane wtyczki, od których pakiet zależy. Weryfikacja przypina ich rekurencyjne skróty
zatwierdzeń; każda zależność musi być już zweryfikowana i włączona. Zmieniona, zaktualizowana, wyłączona lub
unieważniona zależność sprawia, że zależne od niej pakiety stają się niedostępne, także przez narzędzia z pamięci
podręcznej. Dopuszczenie po obliczeniu skrótów ponownie sprawdza stan włączenia i unieważnienia dla całego
przechwyconego domknięcia. Ponowna weryfikacja zależności nie zatwierdza po cichu jej nowych bajtów dla pakietu
nadrzędnego: przejrzyj i ponownie zweryfikuj także każdy pakiet nadrzędny. Każdy `approval_digest` wiąże skrót
swojego pakietu i zatwierdzenia zależności, więc zmieniona zależność przechodnia zmienia to zatwierdzenie, nawet gdy
własne pliki pakietu nadrzędnego pozostają identyczne. Cykliczne zatwierdzenie zależności nie może się aktywować.
Domknięcie zależności jest ograniczone do 64 pakietów.

Każde zarejestrowane wywołanie ponownie sprawdza stan cyklu życia i przechwytuje prywatne kopie tylko do odczytu
pakietu i jego przypiętego domknięcia zależności. Kontrakt narzędzia musi pasować do
przechwyconego manifestu. Zarejestrowany handler uruchamia się z tej kopii; edycja źródła
po przechwyceniu nie może zastąpić tego handlera ani jego dołączonych plików. Obserwacje wykonania
zawierają skróty pakietu, rekurencyjnego zatwierdzenia i zależności. Zarejestrowane pakiety wymagają rzeczywistej
izolacji na poziomie jądra, nawet jeśli lokalny wywołujący niskiego poziomu wybrał słabsze nadpisanie.
Hosty bez bezpiecznego przechwytywania deskryptorów bez podążania za dowiązaniami albo bez izolacji na poziomie jądra
odrzucają zarejestrowane pakiety. Limity czasu wywołań są ograniczone do 1–300 sekund; własna grupa procesów
wywołania jest zatrzymywana, zanim jego kopia wykonawcza zostanie usunięta.

Trwałe dane wyjściowe, pamięci podręczne i logi trzymaj w `PLUGIN_SCRATCH`, a nie obok kodu wykonywalnego.
Narzędzia tylko do odczytu nie dostają uprawnienia do zapisu. Inne narzędzia mogą zapisywać w katalogu na dane
robocze, ale nie mogą modyfikować swojej kopii wykonawczej. `VOOL_PLUGIN_DEPENDENCIES` to mapowanie JSON
z zadeklarowanych tożsamości zależności na przechwycone katalogi zależności. Inne biblioteki zewnętrzne
i ich pliki blokad dołącz wewnątrz pakietu; sam plik blokady nie poświadcza środowiska zewnętrznego.
Interpreter środowiska uruchomieniowego i biblioteki systemowe pozostają częścią zaufanej platformy i nie są przedstawiane
jako przejrzane bajty pakietów zewnętrznych. Ta kontrola integralności nie certyfikuje zachowania, nie
poświadcza dowolnego kodu pobranego lub załadowanego spoza zatwierdzonej dystrybucji, nie pośredniczy
w każdym wewnętrznym skutku ani nie zastępuje natywnego zatwierdzania w portfelu.

W macOS kod wtyczek działa w piaskownicy macOS. Klucze trafiają do procedury obsługi tylko przez jej prywatne
wejście, nigdy przez jej środowisko, ponieważ każdy program tego samego użytkownika, w tym kod wtyczek,
może odczytać środowisko startowe innego programu na Macu. Serwer połączeń, który potrzebuje klucza w swoim
środowisku, działa tylko w opcjonalnym, odizolowanym środowisku uruchomieniowym Linux, które przygotowujesz sam;
VOOL nigdy nie uruchamia maszyny wirtualnej ani nie pobiera obrazu. Bez niego taka wtyczka pozostaje wyłączona i podaje powód.
Linux używa bwrap, w tym prywatnej przestrzeni nazw procesów i `/proc`.

W macOS narzędzie, które deklaruje sieć, może łączyć się tylko z prywatnym proxy, które VOOL uruchamia dla każdego
wywołania, a proxy łączy się tylko z zadeklarowanymi hostami. W Linuksie i w odizolowanym środowisku uruchomieniowym
procesy wtyczek nie mają sieci. Żądania natywnych dostawców i skanerów korzystają z osobnego
transportu HTTPS do zadeklarowanych hostów na każdej platformie.

## Pisanie umiejętności

* Aby stworzyć umiejętność, prosząc o to na czacie, zobacz [Stwórz własną umiejętność](../../../guides/make-your-own-skill.md).
* Aby samodzielnie napisać, sprawdzić linterem, zainstalować i przetestować plik SKILL.md, zobacz [Budowanie i testowanie umiejętności](../../../guides/build-and-test-a-skill.md).

---
description: Rozszerzanie VOOL o umiejętności i wtyczki.
translation: machine
source_sha256: a86860b8624e3c97c81e813ec92e3998c687a87d7bc43b7960c489cdcd309196
---

# Umiejętności i wtyczki

**Umiejętność** to spakowany zestaw instrukcji do powtarzalnego zadania. **Wtyczka** dodaje nowe
narzędzia do środowiska uruchomieniowego.

## Wbudowane a instalowane z zewnątrz

VOOL ma **natywną bibliotekę umiejętności** — umiejętności dostarczane razem z samą aplikacją — oraz,
w obecnych kompilacjach, wtyczkę **VOOL Database**. Możesz też instalować własne umiejętności i wtyczki.

| | Wbudowane (dostarczane z VOOL) | Instalowane z zewnątrz |
| --- | --- | --- |
| Gdzie się znajdują | Wewnątrz aplikacji | Twój folder wtyczek (ustawiany w Ustawieniach) |
| Aktualizacje | Razem z aplikacją | Gdy zaktualizujesz pakiet |
| Można wyłączyć | Tak — pojedynczo | Tak — pojedynczo |
| Przyznają uprawnienia | **Nie** | **Nie** |

Dla obu źródeł obowiązują dwie zasady:

* **Umiejętności uczą; nigdy niczego nie przyznają.** Narzędzia zadeklarowane przez umiejętność są sygnałem
  *zawężającym* (budżetem narzędzi danego przepływu pracy), nigdy przyznaniem czegokolwiek. Kontroler uprawnień
  nie odczytuje umiejętności.
* **Jeden spis.** Katalog pokazywany w konsoli i pakiety ładowane przez środowisko uruchomieniowe to
  ta sama lista. To, że umiejętność pojawia się na liście, nie dowodzi, że da się ją załadować — każdy wpis ma
  swój bieżący stan (dostępna, wyłączona, brak wymagania wstępnego, nieprawidłowy kontrakt) i powód.

## Znajdowanie umiejętności

Strona Umiejętności i wtyczki pokazuje najpierw własne umiejętności VOOL. **Przeglądaj** zawiera 53 gotowe
umiejętności VOOL. Są tylko tekstowe: każda z nich to zestaw instrukcji dołączony do aplikacji, więc
Przeglądaj wyświetla je bez wyszukiwania online. Umiejętności VOOL korzystające z narzędzi VOOL są wymienione na
karcie **Kodeks umiejętności**, gdzie na początku są umiejętności zespołu VOOL. Przejrzyj umiejętność, zanim ją
włączysz.

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

## Zatwierdzanie pakietów w v0.7

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
Interpreter hosta i biblioteki systemowe pozostają częścią zaufanej platformy i nie są przedstawiane
jako przejrzane bajty pakietów zewnętrznych. Ta kontrola integralności nie certyfikuje zachowania, nie
poświadcza dowolnego kodu pobranego lub załadowanego spoza zatwierdzonej dystrybucji, nie pośredniczy
w każdym wewnętrznym skutku ani nie zastępuje natywnego zatwierdzania w portfelu.

## Pisanie umiejętności

Umiejętność to folder z plikiem instrukcji i plikami, do których się odwołuje:

```text
my-skill/
├── SKILL.md
└── templates/
```

Szkic przygotuj w aplikacji (`skill.create` go przygotowuje, niczego nie aktywując), sprawdź jego poprawność,
a potem zainstaluj, aby go aktywować — to celowo trzy osobne kroki, aby tworzenie nigdy nie było niejawną
zmianą zachowania. Katalog i format tworzenia są wciąż dopracowywane przed publicznym
wydaniem.

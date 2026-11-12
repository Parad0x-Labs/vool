---
description: Jak zainstalować asystenta AI VOOL na macOS (Apple Silicon lub Intel) albo Linuksie z plików wydania lub ze źródeł na macOS, Windows albo Linuksie, wraz z wymaganiami systemowymi i weryfikacją pobranego pliku.
translation: machine
source_sha256: e0025d89fc56caca91923de6f1e6afe25ba6f6383a300ec3df5a7f7e3f401e55
---

# Instalacja

VOOL 0.7.7-beta ma pliki aplikacji do pobrania dla Maców z Apple Silicon, Maców z procesorem Intel i Linuksa x86_64 w
[wydaniu v0.7.7-beta](https://github.com/Parad0x-Labs/vool/releases/tag/v0.7.7-beta).
Możesz też zainstalować VOOL ze źródeł skryptem startowym na macOS, Linuksie lub Windows.
Szczegóły dotyczące podpisywania i aktualizacji znajdziesz w sekcji [Stan wydań](../../../trust/release-status.md).

## Wymagania

| | Minimum | Komfortowo |
| --- | ---: | ---: |
| Pamięć RAM | 8 GB | 16 GB lub więcej |
| Dysk | 2 GB na aplikację | 20 GB z modelami lokalnymi |
| macOS | 14 Sonoma, Apple Silicon lub Intel ([ograniczenia](#maki-z-procesorem-intel)) | 14 Sonoma lub nowszy, Apple Silicon |
| Windows | 10 (64-bitowy) | 11 |
| Linux | glibc 2.31 | Ubuntu 22.04 lub nowszy |

Modele lokalne potrzebują znacznie więcej pamięci RAM niż sama aplikacja. Wskazówki dotyczące rozmiaru są w sekcji
[Uruchamianie modelu lokalnego](../../../guides/local-models.md).

## Pobieranie aplikacji

| Twój komputer | Plik do pobrania |
| --- | --- |
| Mac z Apple Silicon (M1 i nowsze) | [`VOOL-0.7.7-beta-macos-arm64.dmg`](https://github.com/Parad0x-Labs/vool/releases/download/v0.7.7-beta/VOOL-0.7.7-beta-macos-arm64.dmg) |
| Mac z procesorem Intel | [`VOOL-0.7.7-beta-macos-x86_64.dmg`](https://github.com/Parad0x-Labs/vool/releases/download/v0.7.7-beta/VOOL-0.7.7-beta-macos-x86_64.dmg) |
| Linux x86_64 (Ubuntu 22.04 / 24.04) | [`vool-0.7.7-beta-linux-x86_64.tar.gz`](https://github.com/Parad0x-Labs/vool/releases/download/v0.7.7-beta/vool-0.7.7-beta-linux-x86_64.tar.gz) |

Aby sprawdzić procesor swojego Maca, otwórz menu Apple i wybierz **Ten Mac**. „Apple M…”
oznacza Apple Silicon, a „Intel” oznacza Intel. Aplikacja dla Apple Silicon nie działa na Macu z procesorem Intel
i informuje o tym przy otwarciu.

Przed otwarciem sprawdź SHA-256 pobranego pliku (zobacz [Weryfikacja pobranego pliku](#weryfikacja-pobranego-pliku)).

### Pierwsze otwarcie na Macu

Aplikacja w wersji beta nie jest jeszcze poświadczona notarialnie przez Apple, więc przy pierwszym otwarciu macOS
prosi o potwierdzenie. To normalne i zdarza się tylko raz.

1. Otwórz plik DMG i przeciągnij **VOOL** do folderu **Aplikacje**.
2. Kliknij dwukrotnie VOOL. macOS informuje, że nie może sprawdzić aplikacji pod kątem złośliwego oprogramowania.
   Zamknij komunikat.
3. Otwórz **Ustawienia systemowe → Prywatność i ochrona**. Przewiń w dół do komunikatu o VOOL
   i kliknij **Otwórz mimo to**. Wpisz hasło, aby potwierdzić.

W macOS 14 Sonoma możesz zamiast tego w folderze Aplikacje kliknąć VOOL, trzymając naciśnięty klawisz Control, wybrać
**Otwórz**, a następnie ponownie kliknąć **Otwórz**.

### Linux

Pobierz z wydania archiwum tar i jego plik `.sha256`, sprawdź archiwum, a potem uruchom
dołączony instalator. Nie potrzeba sudo ani systemowego Pythona.

```bash
sha256sum -c vool-0.7.7-beta-linux-x86_64.tar.gz.sha256
tar -xzf vool-0.7.7-beta-linux-x86_64.tar.gz
bash vool-0.7.7-beta-linux-x86_64/install.sh
~/.local/bin/vool
```

VOOL instaluje się w `~/.local/share/vool`, umieszcza polecenie `vool` w `~/.local/bin` i dodaje
pozycję w menu aplikacji. Wersja na komputer otwiera czat w przeglądarce; zamknięcie przeglądarki
nie zatrzymuje VOOL. Aby usunąć VOOL, uruchom `bash ~/.local/share/vool/current/uninstall.sh`.
Twoje czaty i ustawienia zostają w twoim profilu.

Od wersji 0.7.5 wersja dla Linuksa aktualizuje się z poziomu VOOL. Nowa wersja instaluje się obok
starej. Jeśli nie uruchomi się poprawnie, VOOL sam wraca do poprzedniej.

### Po instalacji

Zanim model lokalny będzie mógł odpowiadać, otwórz **Ustawienia → Modele i dostawcy → Modele lokalne** i raz naciśnij dla niego **Certify**. Uruchamia to krótki test na twoim komputerze.

Od wersji 0.7.5 VOOL aktualizuje się sam: gdy włączone jest „Samodzielne łączenie z internetem”, pokazuje powiadomienie i instaluje nową wersję jednym naciśnięciem. Aktualizacja z 0.7.4 lub 0.7.3 na Macu: włącz „Samodzielne łączenie z internetem” (Ustawienia > Prywatność i uprawnienia) i raz naciśnij „Sprawdź aktualizacje”. Z 0.7.4 lub starszej na Linuksie albo z 0.7.2 lub starszej na Macu: pobierz 0.7.7 i zainstaluj ją na obecnej wersji. Twoje czaty i ustawienia
są przechowywane w twoim profilu, poza aplikacją. Funkcje kryptowalutowe są domyślnie wyłączone.

## Instalacja ze źródeł

Uruchom skrypt startowy dla swojej platformy:

```bash
curl -fsSLo bootstrap_vool.sh https://raw.githubusercontent.com/Parad0x-Labs/vool/v0.7.7-beta/installer/bootstrap_vool.sh
bash bootstrap_vool.sh --ref v0.7.7-beta
```

```powershell
Invoke-WebRequest https://raw.githubusercontent.com/Parad0x-Labs/vool/main/installer/bootstrap_vool.ps1 -OutFile bootstrap_vool.ps1
powershell -ExecutionPolicy Bypass -File .\bootstrap_vool.ps1
```

Polecenia dla macOS / Linuksa przypinają zarówno skrypt startowy, jak i pobieranie jego źródeł do `v0.7.7-beta`. Aby wybrać inne wydanie, zmień zarówno tag w adresie URL skryptu, jak i `--ref`. Skrypt startowy przyjmuje nazwę gałęzi lub tag wydania, a nie hash commita.

Instalacja ze źródeł wymaga Pythona 3.11 lub nowszego. Jeśli żaden nie zostanie znaleziony, instalator spróbuje
go skonfigurować. Pliki aplikacji do pobrania zawierają własnego Pythona.

Instalator przygotowuje środowisko Pythona, sprawdza twój sprzęt, konfiguruje model
lokalny i uruchamia lokalne usługi. Zarezerwuj czas i miejsce na dysku na pierwsze pobranie modelu.
Po konfiguracji wnioskowanie modeli lokalnych działa offline; narzędzia internetowe i modele w chmurze potrzebują
połączenia.

## Maki z procesorem Intel

VOOL działa na Macach z procesorem Intel i macOS 14 lub nowszym (na przykład na 13-calowym MacBooku Pro z 2020 roku),
z pliku dla Intela lub ze źródeł. Instalator ze źródeł wykrywa procesor Intel i
instaluje wersje wszystkich zależności dla Intela. Dwa ograniczenia dotyczą tylko Intela:

* **Brak trenowania modeli lokalnych.** PyTorch nie publikuje wersji dla macOS na Intelu od wersji 2.3, więc
  instalator go pomija, a trenowanie LoRA zgłasza, że jest niedostępne. Nie ma to wpływu na czat, pamięć,
  narzędzia ani modele w chmurze.
* **Modele lokalne działają tylko na CPU.** Programy uruchamiające modele lokalne nie mają akceleracji GPU dla
  grafiki Intel, więc lokalne odpowiedzi są kilka razy wolniejsze niż na Apple Silicon. Na Macu z 8 GB używaj
  modelu w chmurze do czatu; przy 16 GB domyślny mały model lokalny instalatora działa, ale wolno.

Na Macach z procesorem Intel modele lokalne działają na procesorze i są wolne. Długa odpowiedź korzystająca z pamięci może przekroczyć limit czasu, więc na razie używaj tam modelu w chmurze. Certyfikacja modeli lokalnych uwzględnia wolniejsze procesory.

Maki z procesorem Intel mają własny plik do pobrania, `VOOL-0.7.7-beta-macos-x86_64.dmg`. Plik dla Apple Silicon
nie działa na Intelu i informuje o tym przy uruchomieniu.

## Weryfikacja pobranego pliku

Przy każdym pliku w wydaniu jest suma kontrolna SHA-256. Zweryfikuj plik przed
jego otwarciem:

```bash
shasum -a 256 <downloaded-artifact>    # macOS
sha256sum <downloaded-artifact>         # Linux
```

```powershell
Get-FileHash -Algorithm SHA256 <downloaded-artifact>
```

Porównaj wynik z sumą kontrolną opublikowaną w tym wydaniu. Jeśli się różnią,
usuń plik i pobierz go ponownie.

## Dalej

Przejdź do sekcji [Pierwsze uruchomienie](first-run.md).

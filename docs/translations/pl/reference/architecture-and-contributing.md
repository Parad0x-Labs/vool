---
description: Jak zbudowany jest VOOL — architektura na jednej stronie, przygotowanie środowiska programisty i sposób współtworzenia.
translation: machine
source_sha256: 7cb91029e16c6510821e1ca26b509da8cdfb63113111619d3a31e364a2f088d3
---

# Architektura i współtworzenie

## Architektura na jednej stronie

VOOL to działające przede wszystkim lokalnie środowisko uruchomieniowe na komputer, z udostępnianym rdzeniem:

```text
┌──────────────────────────────────────────────┐
│ Native window / browser console              │
│  (served pages; the console you use)         │
├──────────────────────────────────────────────┤
│ Served runtime (local HTTP API)              │
│  routing → tool execution → answers          │
├───────────────┬──────────────┬───────────────┤
│ Local models  │ Cloud lanes  │ Wallet (opt)  │
│ (Ollama etc.) │ (your keys)  │ pilot lanes   │
├───────────────┴──────────────┴───────────────┤
│ Records: receipts, ledgers, fault registry   │
│ Local storage (SQLite under your profile)    │
└──────────────────────────────────────────────┘
```

Zasady, których przestrzega kod:

* **Domyślnie lokalnie; chmura przez twój klucz.** Ścieżki chmurowe istnieją tylko z twoimi poświadczeniami
  i w ramach twoich pułapów.
* **Jeden decydent na decyzję.** Uprawnienia, wydatki i ruch wychodzący do sieci mają każde po jednym
  nadrzędnym kontrakcie; błędy są klasyfikowane na granicy, do której należą.
* **Mów, co się nie powiodło.** Nieznane wyniki są oznaczane jako nieznane; odmowy mówią, do czego nie
  doszło; przypięty model nigdy nie jest po cichu zastępowany.
* **Wszystko zapisane.** Wywołania narzędzi, zatwierdzenia, płatności i aktualizacje zapisują potwierdzenia
  w twoim profilu.

## Przygotowanie środowiska programisty

```bash
git clone https://github.com/Parad0x-Labs/vool.git
cd vool
python3 -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
pytest tests -q            # the suite
```

Uwagi dotyczące platform, pełny układ narzędzi i bramki wydania znajdują się w dokumentacji inżynierskiej
repozytorium (`docs/` w katalogu głównym repozytorium; szczegóły wewnętrzne nie są częścią tej publicznej
strony z dokumentacją).

## Dokumentacja: jedno kanoniczne źródło

Ta dokumentacja jest generowana z **jednego** źródła: folderu `docs/` tego repozytorium (układ GitBook,
`.gitbook.yaml` w katalogu głównym repozytorium). Publiczna strona z dokumentacją jest budowana z tego źródła.
Aby pracować nad dokumentacją:

```bash
python3 tools/docs/build_docs.py --src . --out docs-build --base /docs
python3 tools/docs/check_docs_build.py docs-build /docs
python3 -m http.server -d docs-build 8000   # local preview
```

Księga błędów (`ERROR_BOOK.md`) i manifest przekazania lokalizacji są
**generowane z własnych rejestrów środowiska uruchomieniowego** — generuj je ponownie, nigdy nie edytuj ręcznie:

```bash
python -m tools.generate_error_book
python -m tools.generate_localization_handoff
```

Ukierunkowany test powoduje niepowodzenie kompilacji, jeśli wygenerowane pliki odbiegają od swoich rejestrów.

## Współtworzenie

* Małe, przejrzane zmiany; każde twierdzenie w dokumentacji musi zgadzać się z kodem w wydanej postaci.
* Zachowuj stabilne kody i identyfikatory — żyją dłużej niż kompilacje.
* Nigdy nie poszerzaj uprawnień po cichu: nowe uprawnienie, nowa ścieżka wydatków lub nowa ścieżka ruchu
  wychodzącego to zmiana projektu, a nie poprawka.
* Ograniczenia bety są dokumentowane, a nie ukrywane — jeśli znajdziesz nieudokumentowane ograniczenie,
  jest to błąd w dokumentacji.

---
description: O co asystent AI VOOL pyta przy pierwszym otwarciu i czego nie robi.
translation: machine
source_sha256: cedf20410d47a2d277e5522bb3e55169854a91a267d71193a762e072e7f8e47e
---

# Pierwsze uruchomienie

Przy pierwszym uruchomieniu VOOL zadaje cztery krótkie pytania. Każde możesz pominąć, a każdą odpowiedź
zmienić później. Podczas odpowiadania nic nie jest nigdzie wysyłane, poza sprawdzeniem klucza do chmury u jego
dostawcy, jeśli go dodasz.

## Co się dzieje

1. **Gdzie ma myśleć?** Na tym komputerze z modelem lokalnym albo online z kluczem od
   dostawcy, któremu płacisz. Zobacz [Podłączanie modelu](connect-a-model.md).
2. **Wybierz folder.** Folder projektu, w którym pracuje VOOL. Zobacz
   [Obszary robocze](../../../concepts/workspaces.md).
3. **Co może robić sam?** Jak często VOOL zatrzymuje się i pyta cię, zanim cokolwiek zmieni. Zobacz
   [Narzędzia](../../../concepts/tools.md).
4. **Jak ma Cię nazywać?** Opcjonalne. Wystarczy imię lub pseudonim i zostaje ono na
   tym komputerze.

Wskaźnik trybu pokazuje potem Lokalnie, Auto albo nazwę dostawcy chmurowego i odzwierciedla
aktualnie wybrany model.

**Raz certyfikuj model lokalny.** Zanim model lokalny będzie mógł odpowiadać, otwórz
**Ustawienia → Modele i dostawcy → Modele lokalne** i naciśnij dla niego **Certify**. Uruchamia to krótki
test na twoim komputerze.

## Czego VOOL nie robi przy pierwszym uruchomieniu

* Nie zakłada konta.
* Nie wymaga klucza do chmury. Do korzystania z aplikacji wystarczy model lokalny.
* Nie indeksuje twojego dysku. Czyta pliki tylko wtedy, gdy wymaga tego żądanie; foldery, do których ma
  dostęp, opisuje sekcja [Obszary robocze](../../../concepts/workspaces.md).

{% hint style="info" %}
W każdej chwili możesz wybrać inny folder projektu przyciskiem **+ Projekt** na pasku bocznym czatu.
{% endhint %}

## Dalej

Przejdź do sekcji [Podłączanie modelu](connect-a-model.md).

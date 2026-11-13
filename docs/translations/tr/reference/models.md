---
description: VOOL ile hangi yapay zekâ modellerinin çalıştığı: kendi donanımınızdaki yerel modeller ve sağlayıcı hesabınız üzerinden bulut modelleri.
translation: machine
source_sha256: 2ce84ebed0545059de772cb175bd8f088ee2d22feb60d4256ee7800f20097824
---

# Desteklenen modeller

VOOL bir modelle birlikte gelmez; ilk yerel model, onu ilk kullandığınızda indirilir. Seçtiğiniz çalışma ortamlarına ve sağlayıcılara bağlanır.

## Yerel çalışma ortamları

Ollama, llama.cpp, vLLM ve MLX otomatik olarak algılanır. localhost üzerinde OpenAI uyumlu bir uç nokta sunan diğer çalışma ortamlarını elle gösterebilirsiniz.

## Bulut sağlayıcıları

Bulut modellerine kendi anahtarınızla, kendi makinenizden ulaşılır. Birini seçmek uygulamayı bulut moduna geçirir (gösterge sağlayıcının adını gösterir); bkz. [Yerel ve bulut](../concepts/local-and-cloud.md).

[OpenRouter](../guides/openrouter.md) en geniş tekil bağlantıdır: tek anahtar, birçok sağlayıcı.

## Seçim

| İş | Makul seçim |
| --- | --- |
| Özel dosyalar, ağ yok | Yerel, 7B ya da daha büyük |
| Günlük kodlama | Yerel 7–14B kodlama modeli |
| Uzun akıl yürütme | Daha büyük bir bulut modeli |
| Maliyet kontrolü | Önce yerel, değdiğinde bulut |

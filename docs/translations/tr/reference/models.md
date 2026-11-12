---
description: VOOL ile hangi yapay zekâ modellerinin çalıştığı: kendi donanımınızdaki yerel modeller ve sağlayıcı hesabınız üzerinden bulut modelleri.
translation: machine
source_sha256: 1528dfca8e391868d09156c42c1dd51514770ffab17077d9260d248842c82060
---

# Desteklenen modeller

VOOL bir modelle birlikte gelmez. Seçtiğiniz çalışma ortamlarına ve sağlayıcılara bağlanır.

## Yerel çalışma ortamları

localhost üzerinde OpenAI uyumlu bir uç nokta sunan her çalışma ortamı kullanılabilir. Yaygın olanlar otomatik olarak algılanır.

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

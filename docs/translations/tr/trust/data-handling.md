---
description: VOOL'un tam olarak hangi verileri, ne zaman ve kime gönderdiği. Bilgisayarınızda neyin kaldığı ve yapay zekâ sağlayıcınıza neyin ulaştığı.
translation: machine
source_sha256: 4fd0aaa4d2480ae80b94aa7132c46c8263113450e4b02c5747388791e7bc8710
---

# Verilerin işlenmesi

## İki durum

**Yerel** — model ve dosyalarınız bu bilgisayarda kalır. Hava durumu veya fiyatlar gibi güncel bilgiler yalnızca "Web aramaları" açıkken herkese açık web hizmetlerinden gelir (yeni bir kurulumda kapalıdır), "Yalnızca yerel" modunda ise asla gelmez.

**Bulut** — seçilen bağlam doğrudan seçilen sağlayıcıya gönderilir.

Sohbet başlığı bir sonraki isteğin hangi yolla çalışacağını gösterir; örneğin VOOL Auto bu bilgisayardan yanıt verirken **Otomatik** ile **Önce yerel**.

## VOOL'un yapmadıkları

* İstemlerinizi Parad0x Labs altyapısı üzerinden geçirmez.
* Çalışma alanınızı yüklemez.
* Anahtarınızı, ait olduğu sağlayıcı dışında hiçbir yere göndermez.

## Özellikle OpenRouter

OpenRouter hesabınız sizin olarak kalır. VOOL, istekleri sizin anahtarınızla doğrudan bu bilgisayardan
gönderir. Parad0x istemlerinize aracılık etmez ve kullanımınızın ücretini ödemez. Gerçek VOOL
kullanımı, VOOL'un herkese açık toplu OpenRouter istatistiklerinde görünebilir, ancak tek tek istemler
ve kimlikler yayımlanmaz.

## Telemetri

VOOL ürün telemetrisi göndermez. Bir hata raporu makinenizden yalnızca siz onu önizleyip
göndermeyi seçtiğinizde çıkar; bkz. [Hata bildirimi ve gizlilik](bug-reporting.md).

## Sağlayıcınızın koşulları geçerlidir

Bulut modunda verileriniz sağlayıcınıza VOOL'un değil, **onun** saklama ve eğitim
koşullarıyla ulaşır. Seçtiğiniz sağlayıcının koşullarını okuyun.

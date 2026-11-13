---
description: VOOL hakkında sık sorulan sorular: çevrimdışı çalışıp çalışmadığı, API anahtarınızın nerede saklandığı, ücretsiz olup olmadığı ve hangi sistemleri desteklediği.
translation: machine
source_sha256: 4366d0e907cc16c3206be768f984f5a525f4187024d16aae541a2ec8937e0aa0
---

# SSS

## VOOL çevrimdışı çalışır mı?

Evet, yerel bir modelle, sohbetleriniz ve dosyalarınız için. Bulut modelleri, tanımları gereği bir ağ bağlantısına ihtiyaç duyar. Hava durumu veya fiyatlar gibi güncel bilgiler yalnızca "Web aramaları" açıkken herkese açık web hizmetlerinden gelir (yeni bir kurulumda kapalıdır), "Yalnızca yerel" modunda ise asla gelmez.

## Bir bulut anahtarına ihtiyacım var mı?

Hayır. Uygulamayı kullanmak için yerel bir model yeterlidir.

## Anahtarım nerede saklanıyor?

Bilgisayarınızda, VOOL'un "runtime home" klasöründe, yalnızca kullanıcı hesabınızın okuyabildiği şifrelenmiş bir dosyada. VOOL'un bir Mac'te zaten macOS anahtar zincirinde tuttuğu anahtarlar orada kalır. Anahtarlar makinenizde kalır. Bkz. [Güvenlik modeli](../trust/security.md#anahtarlar).

## Parad0x istemlerimi görüyor mu?

Yerel modda hayır, bulut modunda da hayır: bulut istekleri makinenizden sağlayıcınıza gider. Bkz. [Verilerin işlenmesi](../trust/data-handling.md).

## Açarken sistemim neden beni uyarıyor?

macOS uygulamaları ad-hoc imzalıdır ama henüz Apple tarafından onaylanmamıştır (noter tasdikli değildir); bu yüzden macOS ilk açılışı onaylamanızı ister. Adımlar [Kurulum](../getting-started/install.md#macte-ilk-açılış) sayfasındadır. Bkz. [Sürüm durumu](../trust/release-status.md).

## Kendi modelimi kullanabilir miyim?

Evet. OpenAI uyumlu yerel bir uç nokta sunan her çalışma ortamı kullanılabilir. Bkz. [Desteklenen modeller](../reference/models.md).

## Ücretsiz mi?

Uygulama beta süresince ücretsizdir. Bulut modeli kullanımı sağlayıcınız tarafından sizin hesabınıza faturalandırılır.

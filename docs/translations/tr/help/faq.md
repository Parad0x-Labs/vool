---
description: VOOL hakkında sık sorulan sorular: çevrimdışı çalışıp çalışmadığı, API anahtarınızın nerede saklandığı, ücretsiz olup olmadığı ve hangi sistemleri desteklediği.
translation: machine
source_sha256: d98db6dcf1791e9de7aed2dbb7204ab774afd9ea47d883bc0211640a8895413e
---

# SSS

## VOOL çevrimdışı çalışır mı?

Evet, yerel bir modelle. Bulut modelleri, tanımları gereği bir ağ bağlantısına ihtiyaç duyar.

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

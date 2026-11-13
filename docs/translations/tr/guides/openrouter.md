---
description: Tek bir API anahtarıyla birçok bulut yapay zekâ modeline ulaşmak için OpenRouter'ı VOOL'a bağlama.
translation: machine
source_sha256: 75cb20d43eb37adb1d30913959ca1cb46864ad57ba7a1a22ec261f0e0eaf2e9a
---

# OpenRouter bağlama

OpenRouter, tek bir anahtarın arkasında birçok sağlayıcıya erişim sağlar. VOOL, bir OpenRouter anahtarı eklemeyi destekler.

## Anahtarı ekleme

OpenRouter hesabınızda bir anahtar oluşturun, **Ayarlar → API anahtarları** altına yapıştırın ve **Anahtarı kaydet**'e basın. VOOL anahtarı OpenRouter ile bir kez kontrol eder, ardından bu bilgisayarda şifrelenmiş bir dosyada saklar (bkz. [Güvenlik modeli](../trust/security.md#anahtarlar)).

## Faturalandırma ve atıf

{% hint style="info" %}
OpenRouter hesabınız sizin olarak kalır. VOOL, istekleri sizin anahtarınızla doğrudan bu bilgisayardan gönderir. Parad0x istemlerinize aracılık etmez ve kullanımınızın ücretini ödemez. Gerçek VOOL kullanımı, VOOL'un herkese açık toplu OpenRouter istatistiklerinde görünebilir, ancak tek tek istemler ve kimlikler yayımlanmaz.
{% endhint %}

## Anahtarı kaldırma

Anahtarı **Ayarlar → API anahtarları** altında kaldırın. Anahtarın sunucu tarafında da geçersiz olmasını istiyorsanız onu OpenRouter hesabınızda da iptal edin.

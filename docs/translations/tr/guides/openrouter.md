---
description: Tek bir API anahtarıyla birçok bulut yapay zekâ modeline ulaşmak için OpenRouter'ı VOOL'a bağlama.
translation: machine
source_sha256: 3a09fe6c636e2b8dd14a2d627c4653055dbb10bf8fd8221b7a3bcccf3044cb19
---

# OpenRouter bağlama

OpenRouter, tek bir anahtarın arkasında birçok sağlayıcıya erişim sağlar. VOOL, bir OpenRouter hesabı bağlamayı destekler.

## Bağlama

OpenRouter hesabınızda bir anahtar oluşturun, **Ayarlar → API anahtarları** altına yapıştırın ve **Anahtarı kaydet**'e basın. VOOL anahtarı OpenRouter ile bir kez kontrol eder, ardından bu bilgisayarda şifrelenmiş bir dosyada saklar (bkz. [Güvenlik modeli](../trust/security.md#anahtarlar)).

## Faturalandırma ve atıf

{% hint style="info" %}
OpenRouter hesabınız sizin olarak kalır. VOOL, istekleri sizin anahtarınızla doğrudan bu bilgisayardan gönderir. Parad0x istemlerinize aracılık etmez ve kullanımınızın ücretini ödemez. Gerçek VOOL kullanımı, VOOL'un herkese açık toplu OpenRouter istatistiklerinde görünebilir, ancak tek tek istemler ve kimlikler yayımlanmaz.
{% endhint %}

## Bağlantıyı kesme

Anahtarı **Ayarlar → API anahtarları** altında kaldırın. Anahtarın sunucu tarafında da geçersiz olmasını istiyorsanız onu OpenRouter hesabınızda da iptal edin.

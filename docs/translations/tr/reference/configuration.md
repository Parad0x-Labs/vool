---
description: VOOL'un yapılandırmasını nerede sakladığı ve ayarların ne anlama geldiği.
translation: machine
source_sha256: 3bbb23b29003bdd8c973c2d88a34faf974f6436405b7c90da4d0307c0962f887
---

# Yapılandırma

## Konumlar

VOOL ayarlarını, sohbetlerini, hafızasını ve kayıtlarını tek bir "runtime home" klasöründe tutar:

| Kurulum | Runtime home |
| --- | --- |
| macOS uygulaması | `~/Library/Application Support/VOOL/runtime` |
| Linux tar arşivi ve kaynak koddan kurulumlar | `~/.vool_runtime` |
| Windows (deneysel, kaynak koddan) | `%LOCALAPPDATA%\VOOL` |

VOOL'u başlatmadan önce `VOOL_HOME` ayarlanırsa onun yerine o klasör kullanılır. Başlangıç günlükleri kullanılan klasörü yazdırır.

Anahtarlar ayar dosyalarına ya da günlüklere **yazılmaz**. Bu "runtime home" klasöründe, yalnızca kullanıcı hesabınızın okuyabildiği ayrı bir kimlik bilgisi dosyasında şifrelenmiş olarak tutulur; VOOL'un bir Mac'te zaten macOS anahtar zincirinde tuttuğu anahtarlar orada kalır. Bkz. [Güvenlik modeli](../trust/security.md#anahtarlar).

## Ayarlar

Ayarları uygulamada **Ayarlar** altında değiştirin. VOOL'un ne yapabileceğine karar verenler şunlardır:

* **Model** — etkin model; bir turun YEREL mi BULUT mu olacağına o karar verir.
* **Çalışma alanı** — bir sohbetin içinde çalıştığı proje klasörü; VOOL'un erişebildiği diğer klasörler için bkz. [Çalışma alanları](../concepts/workspaces.md).
* **Çalışma modu** — burada değil, ileti kutusunun yanındaki menüden seçilir. Manuel her değişiklikten önce sorar; Otomatik günlük düzenlemelerin ve komutların çalışmasına izin verir, silme, üzerine yazma, kurulum, harcama ve ayarları değiştirme ise yine sorar. Bkz. [İzinler](../concepts/permissions.md).
* **Web erişimi ve kripto** — ikisi de siz açana kadar kapalı kalır.

VOOL'da ürün telemetrisi ayarı yoktur, çünkü VOOL ürün telemetrisi göndermez.

## Sıfırlama

VOOL'dan çıkın ve "runtime home" klasörünü başka bir yere taşıyın. Çalışma alanı dosyalarına dokunulmaz; VOOL yeni bir profille yeniden başlar. Kayıtlı anahtarlarınız da klasörle birlikte taşınır, bu yüzden onları **Ayarlar → API anahtarları** bölümünde yeniden ekleyin. macOS anahtar zincirinde tutulan anahtarlar, siz onları Anahtar Zinciri Erişimi'nde kaldırana kadar orada kalır.

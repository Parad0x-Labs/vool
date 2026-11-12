---
description: VOOL'a bir yapay zekâ modeli bağlama: kendi bilgisayarınızda çalışan yerel bir model ya da kendi API anahtarınızla bir bulut sağlayıcısı.
translation: machine
source_sha256: fdc85c0cf7cf8399666b8dd3d49ddbfb7cbe65ca7800be4630d88bd86053987a
---

# Model bağlama

VOOL bir modelle birlikte gelmez. Bir model siz seçersiniz ve bu seçim, bir isteğin
makinenizde mi kalacağını yoksa makinenizden mi çıkacağını belirler.

## Yerel

Yerel bir çalışma ortamı kuruluysa ve çalışıyorsa VOOL onu algılar ve modellerini listeler.
Birini seçtiğinizde gösterge Yerel olur. Kurulum ayrıntıları
[Yerel model çalıştırma](../guides/local-models.md) sayfasındadır.

## Bulut

**Ayarlar → API anahtarları** bölümünde bir sağlayıcı anahtarı ekleyin. Anahtar bu bilgisayarda şifrelenmiş
bir dosyada saklanır (bkz. [Güvenlik modeli](../trust/security.md#anahtarlar)) ve istekleri
**makinenizden doğrudan sağlayıcıya** göndermek için kullanılır.

Bir bulut modeli seçtiğinizde gösterge sağlayıcının adını gösterir ve bu değişiklik herhangi bir istek
yapılmadan önce gerçekleşir.

OpenRouter'a özgü akış için [OpenRouter bağlama](../guides/openrouter.md) sayfasına bakın.

## Geçiş yapma

Model değiştirmek modu da değiştirir. Esas olan göstergedir: Yerel gösteriyorsa
bir sonraki istek bu bilgisayarda işlenir.

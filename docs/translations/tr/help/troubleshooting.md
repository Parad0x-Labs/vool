---
description: En sık karşılaşılan sorunların çözümleri.
translation: machine
source_sha256: 792c90fd68caf7409212d3742109c46e49d1f7e640aa3e9f70db9fe61ca55d77
---

# Sorun giderme

## macOS uygulamayı açmayı reddediyor

Bu beklenen bir durumdur: uygulama henüz onaylanmamıştır (noter tasdikli değildir). Önce sağlama toplamını kontrol edin, ardından
**Sistem Ayarları → Gizlilik ve Güvenlik**'i açın ve **Yine de Aç**'ı tıklayın. macOS 14'te bunun yerine
Kontrol tuşuna basarak VOOL'u tıklayıp **Aç**'ı seçebilirsiniz.

## Windows

Bu sürümde Windows indirmesi yoktur. Windows'ta kurulum kaynak koddan yapılır ve
deneyseldir; bkz. [kurulum kılavuzu](../getting-started/install.md#kaynak-koddan-kurulum).

## Hiç yerel model listelenmiyor

1. Çalışma ortamının çalıştığından emin olun.
2. En az bir modelin indirildiğinden (pull) emin olun.
3. Bağlantı noktasının VOOL'un beklediğiyle eşleştiğinden emin olun.

```bash
curl http://localhost:11434/api/tags
```

Bu komut hiçbir şey döndürmezse çalışma ortamı çalışmıyordur.

## Yanıtlar yavaş

Genellikle sebep, kullanılabilir belleğe göre modelin boyutudur. Bkz.
[Yerel model çalıştırma](../../../guides/local-models.md).

## Temiz bir başlangıç

VOOL'dan çıkın ve [Yapılandırma](../../../reference/configuration.md) sayfasında belirtilen yapılandırma dizinini
kaldırın. Çalışma alanındaki dosyalara dokunulmaz.

---
description: En sık karşılaşılan sorunların çözümleri.
translation: machine
source_sha256: 809bae11bc4bf8c2d707cfef616f80056a5d98d35b0786cc937a40526e802689
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
[Yerel model çalıştırma](../guides/local-models.md).

## Bir hata kod gösteriyor

Her hatanın sabit bir kodu vardır. [Hatalar ve hata kodları](../reference/errors.md) bir kodun nasıl
okunacağını açıklar; [Hata Kitabı](../ERROR_BOOK.md) ise her kod için ne olduğunu, neyin zaten olmuş
olabileceğini ve sonra ne yapılacağını söyler.

## Temiz bir başlangıç

VOOL'dan çıkın ve [Yapılandırma](../reference/configuration.md) sayfasında belirtilen "runtime home"
klasörünü kaldırın. Bu, sohbetlerinizi, hafızanızı, ayarlarınızı, kayıtlı anahtarlarınızı ve VOOL'un
bu bilgisayarda tuttuğu her cüzdanı siler; bu yüzden önce ihtiyacınız olanı kaydedin: bkz.
[Yedekleme ve kurtarma](backup-and-recovery.md). Çalışma alanındaki dosyalara dokunulmaz.

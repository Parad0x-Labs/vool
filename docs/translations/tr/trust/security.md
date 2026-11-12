---
description: VOOL'un dosyalarınızı ve API anahtarlarınızı nasıl koruduğu: çalışma alanı sınırları, kimlik bilgisi saklama ve bir eklentinin nelere erişebildiği.
translation: machine
source_sha256: 4397ca0e7b494efacb90aa546eae10800b9a58558ba7c611c0131f4410c2fe3a
---

# Güvenlik modeli

## Sınırlar

* **Klasör sınırları** dosya erişimini sınırlar. VOOL, sohbetin proje klasöründeki dosyaları okur ve değiştirir. Onun dışında VOOL görünür ev klasörlerinizi okuyabilir, dosyaları ise yalnızca Masaüstü, İndirilenler ve Belgeler klasörlerinde değiştirebilir; gizli klasörler, uygulama ve sistem verileri ve VOOL'un kendi verileri asla okunmaz. Bkz. [Çalışma alanları](../concepts/workspaces.md).
* **Kimlik bilgisi deposu** anahtarları tutar. Anahtarlar yapılandırma dosyalarına ya da günlüklere yazılmaz.
* **Araç onayı** değişiklikleri sınırlar. Manuel modda her değişiklik önce sorar; Otomatik modda günlük düzenlemeler ve komutlar otomatik olarak çalışabilir, silme, üzerine yazma, kurulum, harcama ve ayarları değiştirme ise yine sorar. Bkz. [Araçlar](../concepts/tools.md).

## Yerel ağınız

VOOL varsayılan olarak yalnızca bu bilgisayarda dinler. Onu yerel ağınızda dinleyecek şekilde ayarladığınızda yalnızca bu bilgisayar siz olarak kabul edilir. Ayarlar, dosyalar, anahtarlar, araçlar ve özel özellikler yalnızca ona yanıt verir.

Ağdaki başka bir cihaz yalnızca düz sohbet alır. Ayrıca bir seferde herkese açık tek bir web sayfası okuyabilir. Bu sayfa herkese açık internette olmalıdır: bu bilgisayardaki ya da yerel ağınızdaki adresler reddedilir ve her yönlendirme aynı şekilde denetlenir. Web aramaları, araştırma, tarayıcı, fiyat teklifleri, ödemeler ve ücretli anahtarlarınız yalnızca bu bilgisayarda çalışır.

## Discord köprüsü

Discord köprüsü yalnızca sahibine yanıt verir. Ona ilk komutu gönderen Discord kullanıcısı sahibi olur. Sahibi kendiniz seçmek için `VOOL_DISCORD_OWNER_USER_ID` değerini Discord kullanıcı kimliğinize ayarlayın; başkalarının yazabildiği her kanal için bunu yapın. Diğer herkese bir kez bu VOOL'un sahibine kilitli olduğu söylenir ve iletileri işlenmez. Sahibin Discord iletileri bile ağdaki başka bir cihazla aynı sınırlara tabidir.

## Anahtarlar

VOOL sağlayıcı anahtarlarınızı bu bilgisayardaki "runtime home" klasöründe (bkz. [Yapılandırma](../reference/configuration.md)), yalnızca kullanıcı hesabınızın okuyabildiği şifrelenmiş bir dosyada tutar. VOOL'un bir Mac'te zaten macOS anahtar zincirinde tuttuğu anahtarlar orada okunabilir kalır. Bir anahtar, sağlayıcınıza ulaşmak için makinenizden kullanılır. Anahtar Parad0x Labs'e gönderilmez ve istek yolunda Parad0x tarafından işletilen bir uç nokta yoktur.

{% hint style="info" %}
Şifrelenmiş dosya ayrı bir parolayla değil, kullanıcı hesabınızın dosya izinleriyle korunur. Kaybolan ya da çalınan bir disk anahtarlarınızı açığa çıkarmasın diye disk şifrelemesini açık tutun (Mac'te FileVault).
{% endhint %}

## Eklentiler

Bir eklenti, çalışma zamanının erişimiyle çalışır. Eklentileri kullanışlı yapan da, kurmadan önce okunmaya değer kılan da budur. Bkz. [Beceriler ve eklentiler](../guides/skills-and-plugins.md).

## Bir sorunu bildirme

Güvenlik sorunlarını asla herkese açık bir issue'da değil, [GitHub Security Advisories](https://github.com/Parad0x-Labs/vool/security/advisories/new) üzerinden gizli olarak bildirin. Sürümü, platformu ve sorunu yeniden oluşturma adımlarını ekleyin.

{% hint style="warning" %}
macOS uygulamaları ad-hoc imzalıdır ve Apple tarafından onaylanmamıştır (noter tasdikli değildir); Linux tar arşivi imzasızdır. Bkz. [Sürüm durumu](release-status.md).
{% endhint %}

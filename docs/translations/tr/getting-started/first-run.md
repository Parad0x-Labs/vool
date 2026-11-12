---
description: Yapay zekâ asistanı VOOL'u ilk kez açtığınızda size neler sorduğu ve neler yapmadığı.
translation: machine
source_sha256: cedf20410d47a2d277e5522bb3e55169854a91a267d71193a762e072e7f8e47e
---

# İlk çalıştırma

İlk açılışta dört kısa soru sorulur. Hepsini atlayabilir ve her yanıtı daha sonra
değiştirebilirsiniz. Siz yanıtlarken hiçbir yere hiçbir şey gönderilmez; tek istisna, bir bulut anahtarı
eklerseniz o anahtarın sağlayıcısında doğrulanmasıdır.

## Neler olur

1. **Nerede düşünmeli?** Bu bilgisayarda yerel bir modelle ya da ücretini ödediğiniz bir sağlayıcının
   anahtarıyla çevrimiçi. Bkz. [Model bağlama](connect-a-model.md).
2. **Bir klasör seçin.** VOOL'un içinde çalışacağı proje klasörü. Bkz.
   [Çalışma alanları](../concepts/workspaces.md).
3. **Tek başına ne yapabilir?** VOOL'un herhangi bir şeyi değiştirmeden önce ne sıklıkla durup size soracağı. Bkz.
   [Araçlar](../concepts/tools.md).
4. **Size ne diye çağırsın?** İsteğe bağlı. Bir ad ya da takma ad yeterlidir ve bu bilgi bu bilgisayarda
   kalır.

Ardından mod göstergesi Yerel, Otomatik ya da bulut sağlayıcısının adını gösterir ve o anda seçili olan
modeli yansıtır.

**Yerel bir modeli bir kez sertifikalayın.** Yerel bir modelin yanıt verebilmesi için önce
**Ayarlar → Modeller ve sağlayıcılar → Yerel modeller**'i açın ve o model için **Certify**'a basın. Bu,
makinenizde kısa bir denetim çalıştırır.

## VOOL'un ilk çalıştırmada yapmadıkları

* Hesap oluşturmaz.
* Bulut anahtarı istemez. Uygulamayı kullanmak için yerel bir model yeterlidir.
* Diskinizi dizinlemez. Dosyaları yalnızca bir istek gerektirdiğinde okur; erişebileceği klasörler için
  bkz. [Çalışma alanları](../concepts/workspaces.md).

{% hint style="info" %}
Sohbet kenar çubuğundaki **+ Proje** ile istediğiniz zaman başka bir proje klasörü seçebilirsiniz.
{% endhint %}

## Sonraki adım

[Model bağlama](connect-a-model.md) ile devam edin.

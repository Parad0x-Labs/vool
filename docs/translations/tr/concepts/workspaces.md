---
description: Çalışma alanı, VOOL'un içinde çalıştığı proje klasörüdür. Onun dışında VOOL görünür ev klasörlerinizi okuyabilir, dosyaları ise yalnızca Masaüstü, İndirilenler ve Belgeler klasörlerinde değiştirebilir.
translation: machine
source_sha256: 33b648a0a55f99b1c96365553942911862672eafa55e94bd45f18ce566d5fcea
---

# Çalışma alanları

Çalışma alanı, bir sohbetin içinde çalıştığı proje klasörüdür. VOOL orada dosyaları okur ve değiştirir; çalışma modunuz gerektirdiği her durumda önce size sorar (bkz. [Araçlar](tools.md)).

## Ekleme ve kaldırma

Sohbetteki **Bir proje klasörü seçin…** ile ya da sohbet kenar çubuğundaki **+ Proje** ile bir klasör seçin. Bir projeyi sildiğinizde VOOL o projede öğrendiklerini unutur ve projenin sohbetleri Genel'e geri taşınır. Dosyalarınıza dokunulmaz.

## Diğer klasörleriniz

Çalışma alanının dışında VOOL'un dosya araçları, sabit sınırlarla ev klasörünüze de erişebilir:

| Klasörler | VOOL'un yapabilecekleri |
| --- | --- |
| Masaüstü, İndirilenler, Belgeler | Okuma ve dosyaları değiştirme; çalışma modunuzun dediği gibi önce sorarak |
| Resimler, Filmler (Linux'ta Videolar), Müzik | Yalnızca okuma: listeleme, bulma, açma ve ölçme; asla değiştirme |
| Diğer görünür ev klasörleriniz, örneğin `~/Projects` | Yalnızca okuma |
| Gizli klasörler (örneğin `~/.ssh`), uygulama ve sistem verileri (`Library`, `Applications`, `AppData`), VOOL'un kendi veri klasörü | Asla |

VOOL bu klasörlere yalnızca bir istek gerektirdiğinde bakar; örneğin "vergi PDF'imi bul" ya da "Filmler'deki en büyük video hangisi". Diskinizi arka planda dizinlemez.

## Kapsam önerileri

* Çalışma alanını ev dizininize değil, bir projeye yöneltin.
* Kimlik bilgisi dosyalarını, anahtar malzemesini ve parola depolarını Masaüstü, İndirilenler, Belgeler ya da bir proje klasöründe açıkta bırakmayın; bir parola yöneticisinde ya da gizli bir klasörde tutun.
* Her iş için ayrı bir proje kullanın; böylece birinin bağlamı diğerine ulaşmaz.

{% hint style="warning" %}
VOOL'un proje klasöründe ya da ev klasörlerinizde okuduğu her şey, bir bulut modeli seçiliyken sağlayıcınıza gönderilenlere dahil olabilir. Kapsamı bilinçli olarak belirleyin.
{% endhint %}

---
description: VOOL'u kullanırken hangi verilerin bilgisayarınızdan çıktığı ve ne zaman çıktığı. Yapay zekâyı yerel olarak çalıştırmakla bir bulut modeli kullanmak arasındaki fark.
translation: machine
source_sha256: 2c2b9e26e99579c00a032dfeac95fd216fe9229497c965c1bc08aface7a6c071
---

# Yerel ve bulut

VOOL iki yolla yanıt verebilir: bu bilgisayarda ya da bir bulut sağlayıcısı üzerinden. Sohbet başlığı bir sonraki isteğin hangi yolla çalışacağını gösterir; örneğin VOOL Auto bu bilgisayardan yanıt verirken **Otomatik** ile **Önce yerel**.

## Yerel

Model ve dosyalarınız bu bilgisayarda kalır. Model yerel bir çalışma ortamında çalışır, dosyalar diskten okunur ve hiçbir sağlayıcıya istek yapılmaz. Hava durumu veya fiyatlar gibi güncel bilgiler yalnızca "Web aramaları" açıkken herkese açık web hizmetlerinden gelir (yeni bir kurulumda kapalıdır), "Yalnızca yerel" modunda ise asla gelmez.

## Bulut (sağlayıcı adı)

Seçilen bağlam, makinenizden ve sizin anahtarınızla, seçtiğiniz sağlayıcıya doğrudan gönderilir.

"Seçilen bağlam", uygulamanın o istek için bir araya getirdiği şeydir: isteminiz ve isteğin ihtiyaç duyduğu dosyalar ile önceki turlar.

{% hint style="danger" %}
Bulut modu verileri makinenizin dışına gönderir. Bulut modunun amacı budur. VOOL'un iddiası, hiçbir şeyin iletilmediği değil, *yol* ile ilgilidir: istek bilgisayarınızdan sağlayıcınıza gider.
{% endhint %}

## Arada ne var

Parad0x Labs tarafından işletilen hiçbir şey. Model trafiği için bir proxy, ağ geçidi ya da aktarıcı yoktur. Anahtarınız, sağlayıcıyla doğrudan konuşmak için makinenizdeki uygulama tarafından kullanılır.

Tüm ayrıntılar: [Verilerin işlenmesi](../trust/data-handling.md).

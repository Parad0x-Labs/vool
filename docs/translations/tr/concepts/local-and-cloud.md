---
description: VOOL'u kullanırken hangi verilerin bilgisayarınızdan çıktığı ve ne zaman çıktığı. Yapay zekâyı yerel olarak çalıştırmakla bir bulut modeli kullanmak arasındaki fark.
translation: machine
source_sha256: 3217e13bf6d684853b92d19097627d07e64bdd17745560b1204871cb0b7b0753
---

# Yerel ve bulut

VOOL'un iki durumu vardır. Arayüz hangisinin etkin olduğunu her zaman gösterir.

## Yerel

Her şey bu bilgisayarda kalır. Model yerel bir çalışma ortamında çalışır, dosyalar diskten okunur ve hiçbir sağlayıcıya istek yapılmaz.

## Bulut (sağlayıcı adı)

Seçilen bağlam, makinenizden ve sizin anahtarınızla, seçtiğiniz sağlayıcıya doğrudan gönderilir.

"Seçilen bağlam", uygulamanın o istek için bir araya getirdiği şeydir: isteminiz ve isteğin ihtiyaç duyduğu dosyalar ile önceki turlar. Göndermeden önce gösterilir.

{% hint style="danger" %}
Bulut modu verileri makinenizin dışına gönderir. Bulut modunun amacı budur. VOOL'un iddiası, hiçbir şeyin iletilmediği değil, *yol* ile ilgilidir: istek bilgisayarınızdan sağlayıcınıza gider.
{% endhint %}

## Arada ne var

Parad0x Labs tarafından işletilen hiçbir şey. Model trafiği için bir proxy, ağ geçidi ya da aktarıcı yoktur. Anahtarınız, sağlayıcıyla doğrudan konuşmak için makinenizdeki uygulama tarafından kullanılır.

Tüm ayrıntılar: [Verilerin işlenmesi](../trust/data-handling.md).

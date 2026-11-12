---
description: Beş mod, her birinin VOOL'un sormadan neler yapmasına izin verdiği ve her onay düğmesinin neyi kapsadığı.
translation: machine
source_sha256: 1f33c8d63bcdeddbd1ac44d3b2d948b354137954ee6accb6ee18eeec8bdda1c6
---

# İzinler

VOOL, herhangi bir şey çalışmadan önce neyin çalışabileceğine karar verir. Model bir eylem isteyebilir; bunun olup olmayacağına seçtiğiniz mod ve yanıtlarınız karar verir. Okumak, değiştirmekten farklı ele alınır; gizli bilgiler, ödemeler ve güvenlik ayarları her modda korunur.

## Modlar

Modu ileti kutusunun yanındaki menüden seçin. Yeni bir sohbet **Manuel** modda başlar.

| Mod | Sormadan çalışır | Önce sorar | Asla |
| --- | --- | --- | --- |
| Manuel | Dosyaları okuma ve listeleme, güvenli komutlar ve herkese açık, salt okunur web aramaları | Her değişiklik: dosya düzenlemeleri, komutlar, ağ eylemleri, ayarlar, Git, dağıtım ve iletiler | Saklanan gizli bilgileri okuma |
| Düzenlemeleri incele | Manuel ile aynı | Manuel ile aynı. Her düzenleme grubu, uygulanmadan önce incelenebilir bir değişiklik olarak gösterilir | Saklanan gizli bilgileri okuma |
| Plan | Dosyaları okuma ve listeleme ve güvenli komutlar | Hiçbir şey: yalnızca okur ve bir plan önerir | Her değişiklik, her ağ eylemi |
| Otomatik | Dosyaları okuma, oluşturma ve düzenleme, komutlar, ağ ve tarayıcı eylemleri ve model sağlayıcıları | Silme, var olan dosyaların üzerine yazma, bağımlılık kurma, sağlayıcı ayarlarını değiştirme, Git commit, push, merge ve reset, dağıtım, ileti gönderme, ödemeler ve ayarları değiştirme | Saklanan gizli bilgileri okuma, güvenlik ayarlarını değiştirme ve VOOL'un etkisini belirleyemediği her eylem |
| İzinleri atla | Dört korumalı sınıf dışında her şey | Ödemeler | Saklanan gizli bilgileri okuma, güvenlik ayarlarını değiştirme ve VOOL'un etkisini belirleyemediği her eylem |

Bir mod, bir beceri ya da bir eklenti yalnızca sunulanı daraltabilir. Hiçbiri, modun izin vermediği bir eyleme izin vermez.

## Bir izin isteğini yanıtlama

Bir eylem size ihtiyaç duyduğunda ileti kutusunun üstünde bir **İzin gerekiyor** çubuğu açılır. Çubuk eylemi tam olarak adlandırır: çalışacak komut satırını ya da değişecek dosyaları, değişikliğin geri alınıp alınamayacağını ve düzenlemelerin bir önizlemesini. Siz yanıtlayana kadar hiçbir şey çalışmaz ve VOOL yeniden başlasa bile istek sizi bekler. Süresi dolan bir istek süresi dolmuş olarak görünür ve onaylanamaz.

Çubuk yalnızca eyleme uyan seçenekleri sunar:

| Düğme | Neye izin verir |
| --- | --- |
| Bir kez izin ver | Yalnızca bu tam eyleme. |
| Bu görev için izin ver | Aynı tam eyleme, bu görev sırasında ve aynı modda yeniden. Dosya düzenlemesi olmayan eylemler için sunulur. |
| Bu istekteki tüm *N* planlanan değişiklik için izin ver | Bu isteğin zaten planladığı dosya değişikliklerine, yani "Ayrıntıları incele" altında listelenen dosyaların tam olarak kendisine ve başka hiçbir şeye. |
| Bu sohbette çalışma alanı düzenlemelerine izin ver | Bu sohbetin çalışma alanı içindeki okumalara, yeni dosyalara ve düzenlemelere; sekiz saat boyunca ya da VOOL yeniden başlayana veya sohbet silinene kadar. |
| Bu projede izin ver | Projenin klasörü içindeki okumalara ve dosya yazmalarına. Bu izin proje için geçerli kalır; yeniden başlatmadan sonra ve yeni sohbetlerde de. |
| Ayrıntıları incele | Karar vermeden önce isteğin tamamını gösterir. |
| Reddet | Eylem çalışmaz. |

Neye izin verirseniz verin, mod bunlara kendisi izin vermedikçe silmeler, taşımalar, üzerine yazmalar, komutlar, ağ eylemleri, gizli bilgiler ve ödemeler her seferinde yine ayrıca sorar.

## İzinleri atla

İzinleri atla modu, kullanılabilir araçları sizin seçtiğiniz bir kapsam içinde ve bir süre boyunca tek tek sormadan çalıştırır:

* **Kapsam:** yalnızca geçerli görev, bu sohbet oturumu ya da bu proje.
* **Süre:** 15 ya da 30 dakika; 1, 2, 4 ya da 8 saat; 15 ile 1440 dakika arasında özel bir süre ya da yalnızca bu sohbet için *Ben kapatana dek*.

Süreli atlama her sohbette çalışır. *Ben kapatana dek* için sohbetin bir proje klasörüne bağlı olması gerekir ve bu klasör ev klasörünüzün tamamı, diskin en üstü ya da VOOL'un kendi verilerini tutan bir klasör olamaz. Atlama açıkken bir şerit kapsamını ve ne zaman biteceğini gösterir; şeritteki **×** atlamayı hemen kapatır ve sohbet Manuel moda döner. Sohbeti silmek de atlamayı sona erdirir. Atlama, çalışma alanının dışına asla erişim vermez ve bir modelin kendi izinlerini genişletmesine izin veremez. Ödemeler yine sorar; gizli bilgiler ve güvenlik ayarları erişilemez kalır.

## Ödemeler ve harcama

Bir eylemi onaylamak asla bir ödemeyi onaylamak değildir. Ödemeler her modda, sınırlarıyla birlikte, cüzdanın kendi öneri ve onay adımından geçer. Ücretli model çağrıları, sağlayıcıyla iletişim kurulmadan önce harcama tavanlarınız ve fiyat korumalarınız tarafından denetlenir. Bkz. [Cüzdan](../guides/wallet.md) ve [Harcama sınırları ve fiyat korumaları](../guides/spending-limits.md).

## Bir eylem reddedildiğinde

Reddedilen bir eylem hiçbir şeyi değiştirmemiştir ve ileti neyin neden reddedildiğini söyler. Görebileceğiniz kodlar şunlardır: [`permission_denied`](../ERROR_BOOK.md#permission-denied), izin verilen klasörün dışındaki bir değişiklik için [`confinement_refusal`](../ERROR_BOOK.md#confinement-refusal) ve bu modun sunmadığı bir araç için [`tool_unavailable`](../ERROR_BOOK.md#tool-unavailable). Bkz. [Hatalar ve hata kodları](../reference/errors.md).

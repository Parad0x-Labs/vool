---
description: VOOL'un bu sürümünde neleri kaydedebileceğiniz, taşıyabileceğiniz ve geri yükleyebileceğiniz: sohbetler, hafıza, anahtarlar ve cüzdanlar.
translation: machine
source_sha256: 3b2f11975a8cf5f5ab2e07e8b4a3083a9598de9e301965c493408005aa5893fa
---

# Yedekleme ve kurtarma

VOOL'un tuttuğu her şey bilgisayarınızda, tek bir profil klasöründe durur (nerede olduğu için bkz. [Yapılandırma](../reference/configuration.md)). Uygulamayı güncellemek ya da yeniden kurmak ona asla dokunmaz. VOOL'da verilerinizin bulut kopyası ve profilin tamamının tek adımda yedeği yoktur. Önemli olanı kaydetmenin ve geri getirmenin yolları şunlardır.

## Sohbetler

| İstediğiniz | Kullanın | Elde ettiğiniz |
| --- | --- | --- |
| Okumak ya da paylaşmak için bir kopya | Sohbet başlığındaki **Dışa aktar** | Sohbetin tamamı, metin ya da Markdown olarak. Ekli dosyalar yalnızca adlarıyla listelenir. |
| VOOL'a geri getirmek için bir kopya | Ayarlar, Yedekleme ve geri yükleme, **Sohbeti dışa aktar** | Bu bilgisayar tarafından imzalanmış bir `.voolsession` dosyası olarak tek bir sohbet. |
| O dosyadaki bir sohbeti geri almak | Ayarlar, Yedekleme ve geri yükleme, **Sohbeti içe aktar** | Diğer sohbetlerinizin yanına eklenen sohbet. |
| Diğer yapay zekâ uygulamalarındaki sohbetleriniz | Ayarlar, Yedekleme ve geri yükleme, **Diğer yapay zekâ uygulamalarındaki sohbetlerinizi getirin** | Adları ve iletileriyle o sohbetler. |

**Sohbeti dışa aktar**; sohbetin iletilerini, makbuzlarını ve kanıt başvurularını taşır, asla VOOL'un iç akıl yürütmesini, ayarlarınızı, hafızanızı ya da anahtarlarınızı taşımaz. Dosya bu bilgisayardan çıkacaksa onu bir parola ifadesiyle koruyun: parola ifadesi olmayan bir dışa aktarma önce onaylamanızı ister. Bir önizleme, herhangi bir şey yazılmadan önce tam olarak neyin yazılacağını gösterir.

**Sohbeti içe aktar**, herhangi bir şey eklenmeden önce dosyanın neyi içerdiğini ve kimin imzaladığını gösterir. VOOL'un tanımadığı bir anahtarla imzalanmış bir dosya, siz kabul etmedikçe reddedilir ve sohbet güvenilmeyen olarak işaretli kalır. İçe aktarma, zaten sahip olduğunuz bir sohbeti asla değiştirmez: aynı sohbet burada varsa kopya yeni bir sohbet olarak gelir.

**Diğer yapay zekâ uygulamalarındaki sohbetlerinizi getirin**, bu bilgisayardaki bir uygulamayı ya da bir veri dışa aktarma dosyasını okur. İçeri alınacak sohbetleri siz işaretlersiniz. Hiçbir şey bilgisayarınızdan çıkmaz ve diğer uygulamanın dosyaları asla değiştirilmez. VOOL bu sohbetleri geçmiş olarak tutar: onlardan öğrenmez ve içlerinde yazan hiçbir şeye uymaz.

## Hafıza

Ayarlar, Bellek ve kişiselleştirme, **Profili dışa aktar**, VOOL'un sizin hakkınızda hatırladıklarını `vool-memory-` ve tarihle adlandırılmış bir dosya olarak kaydeder. Bu dosya asla bir anahtar ya da parola içermez. Okumak ve saklamak için bir kopyadır: VOOL'da onun için bir içe aktarma yoktur.

## Model sağlayıcılarının anahtarları

Sağlayıcı anahtarları hiçbir dışa aktarmada yer almaz. Yeni bir bilgisayarda onları Ayarlar'da yeniden ekleyin.

## Cüzdanlar

* **Bir ağ için oluşturulmuş bir VOOL Cüzdanı**, kurulum sırasında ve yalnızca o zaman, bir özel anahtar yedeği gösterir. Onu çevrimdışı saklayın: ona sahip olan herkes cüzdanı denetler. Cüzdanın PIN'ini ya da parolasını unutursanız, Ayarlar, Kripto bölümünde cüzdandaki **Forgot PIN or password?** o yedekle erişimi geri getirir ve yeni bir PIN ya da parola seçersiniz.
* **Eski cep**, oluşturulduğunda bir kez gösterilen kurtarma ifadesiyle korunur. Ayarlar, Kripto, Diğer hesaplar bölümündeki **Restore from phrase…** onu ifadeden geri getirir. Eski cep ayrıca, yalnızca Touch ID ya da Mac parolanızla açılan **Export private key** ile özel anahtarını Phantom'a ya da MetaMask'e aktarabilir.
* Yalnızca izlediğiniz ya da başka bir uygulamadan bağladığınız bir cüzdan VOOL'da hiçbir anahtar tutmaz: burada yedeklenecek bir şey yoktur.

Tüm resim için bkz. [Cüzdan](../guides/wallet.md).

## Bir güncelleme başarısız olursa

Herhangi bir adımda başarısız olan bir güncelleme uygulamayı olduğu gibi bırakır. Verilerinizi yeni sürüme taşımak başarısız olursa verileriniz olduğu gibi geri konur ve VOOL önceki sürümle başlar. Bkz. [Güncellemeler ve geçiş](../reference/updates-migration.md).

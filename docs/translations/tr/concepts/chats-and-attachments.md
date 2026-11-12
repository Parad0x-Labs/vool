---
description: Sohbetler, özel sohbetler ve onlara getirdiğiniz dosyalar ile uzun yapıştırmalar: sınırlar, neyin saklandığı ve neyin reddedilebileceği.
translation: machine
source_sha256: 1497140fef69088f8e0210aeb8a7f6abba6ae82d5132841ce397fe60fa1bab99
---

# Sohbetler ve ekler

Sohbet, VOOL ile yapılan tek bir konuşmadır. Sohbetler bilgisayarınızda saklanır. Bir sohbet bir proje klasörüne bağlanabilir; böylece araçları o klasörde çalışır (bkz. [Çalışma alanları](workspaces.md)). Sohbete dosyaları ek olarak da getirebilirsiniz.

## Dosya ekleme

İleti kutusunun yanındaki **Ekle**'yi kullanın ya da dosyaları kutunun üzerine sürükleyin. Her dosya bir çip olarak görünür ve göndereceğiniz bir sonraki iletiyle birlikte gider. Kutunun altındaki satır sınırları gösterir:

* İleti başına en fazla 8 dosya, dosya başına 10 MB ve ileti başına 25 MB.
* Metin ve kod dosyaları ile PNG, JPEG, GIF ve WebP görselleri. Bu bilgisayarda okumak için gerekenler varsa PDF, Word (DOCX), ZIP gibi arşivler ve video da bunlara katılır. Dosya seçici yalnızca bu bilgisayarın okuyabildiği dosyaları sunar.

Modelin aldığı da sınırlıdır: bir dosyanın en fazla 120.000 karakteri ve bir iletideki tüm dosyaların en fazla 240.000 karakteri. Daha uzun bir dosya bir satırın sonunda kesilir ve kayıt nerede kesildiğini söyler. Kullandığınız model görselleri okuyamıyorsa çip bunu söyler ve görsel modele gönderilmez.

Bir görsel saklanmadan önce gizli ayrıntıları kaldırılır: kamera ve konum verileri (EXIF, XMP, IPTC) ve PNG ile WebP dosyalarının içinde tutulan metin.

Seçtiğiniz bir dosya yalnızca o ileti için kullanılır. Yanıt tamamlandığında dosyanın içeriği silinir ve dosyanın bir makbuzu saklanır.

## Uzun yapıştırmalar

4.000 veya daha fazla karakter yapıştırırsanız VOOL bunu bir ileti olarak değil, sohbetin bir belgesi olarak, tam yapıştırdığınız gibi saklar. **Önizleme** belgenin başlangıcını gösterir. Belge, siz onu ya da sohbeti silene kadar sohbette kalır. Aynı sohbetteki sonraki sorular onu kullanabilir: VOOL modele sohbetin önceki belgelerinden, en yenisinden başlayarak, en fazla 60.000 karakter verir ve nerede kestiğini söyler.

## Bir dosya reddedildiğinde

Reddedilen bir dosya asla sessizce atılmaz. Çipi yerinde kalır ve nedenini söyler; dosyayı kaldırana ya da yeniden deneyene kadar ileti gönderilemez: *Göndermeden önce başarısız eki kaldır veya yeniden dene.* Sık görülen nedenler:

* **Çok büyük ya da çok fazla dosya.** Bazılarını kaldırın ya da dosyaları iki iletide gönderin.
* **VOOL'un okuduğu bir tür değil ya da adının söylediği şey değil.** Adı `.txt` olan ama aslında bir görsel ya da program olan bir dosya veya bu bilgisayarda okuyucusu olmayan bir PDF, nedeniyle birlikte reddedilir.
* **İçinde bir kimlik bilgisi.** Özel anahtar bloğu ya da erişim belirteci gibi, geçerli bir anahtar veya belirteç taşıyor gibi görünen bir dosya ya da yapıştırma reddedilir ve hiçbir şey kaydedilmez. Gizli bilgiyi kaldırın, sonra yeniden ekleyin ya da yapıştırın.

Bu retlerin, iletiyle birlikte gösterilen kendi kısa kodları vardır. Bu kodlar, uygulamanın diğer hatalarını kapsayan [Hata Kitabı](../ERROR_BOOK.md) içinde yer almaz.

## Özel sohbetler

Sohbette "özel sohbet", "bunu kaydetmeyin" ya da "aramızda kalsın" deyin; sohbet o iletiden itibaren kaydetmeyi bırakır: ne iletiler ne bir özet ne de hafıza. *Özel sohbet: hiçbir şey kaydedilmez* yazan bir şerit görünür ve her özel iletide küçük bir hayalet bulunur. Özel iletiler yeniden yüklemeden sonra geri gelmez ve VOOL onları daha sonra hatırlayamaz. Sohbeti bir sonraki iletiden itibaren yeniden kaydetmek için "kaydı yeniden aç" deyin ya da **Kaydı yeniden aç**'a basın. Özel bir sohbet, VOOL yeniden başladıktan sonra da özel kalır.

## Bir sohbeti yanınızda götürme

Sohbet başlığındaki **Dışa aktar**, sohbetin tamamını her iletisiyle birlikte metin ya da Markdown olarak kaydeder; ekli dosyalar adlarıyla listelenir, içerikleri ise asla dahil edilmez. Bir sohbeti başka bir bilgisayara taşımak ya da imzalı bir kopyasını saklamak için bkz. [Yedekleme ve kurtarma](../help/backup-and-recovery.md).

---
description: Bir VOOL hatasının nasıl okunacağı, bir şeyin zaten olup olmadığı ve her kodun nerede açıklandığı.
translation: machine
source_sha256: ccb01d4c778254a055fd67f46241c670aac90cfc83f168b923a19cef1f949de2
---

# Hatalar ve hata kodları

Bir şey başarısız olduğunda VOOL ne olduğunu, neyin zaten olmuş olabileceğini ve sonra ne yapılacağını sade sözcüklerle söyler. Her başarısızlığın ayrıca sabit bir kodu vardır. Bu kodu bir hatanın altındaki teknik ayrıntılarda, komut satırında (`wallet_export_refused: …`) ve API yanıtlarında bulursunuz. Bu sayfa bir hatanın nasıl okunacağını açıklar. [Hata Kitabı](../ERROR_BOOK.md) ise her kodu açıklar.

## Bir hatayı okuma

Yeniden denemeden önce üç şeyi kontrol edin:

1. **Ne oldu.** İleti. Asla iç yollar ya da gizli bilgiler içermez.
2. **Neler zaten olmuş olabilir.** Retlerin çoğu herhangi bir şey gönderilmeden ya da değiştirilmeden önce durur ve ileti bunu söyler. Bazı başarısızlıklar bir eylemin tamamlanıp tamamlanmadığını bilemez; örneğin bir istek gittikten sonraki bir zaman aşımı. O zaman ileti sonucun bilinmediğini söyler ve yeniden denemeden önce kontrol etmenizi ister. Bir ödeme asla yanlışlıkla iki kez gönderilmez: aynı ödemenin ikinci kez gönderilmesi [`wallet_duplicate_payment`](../ERROR_BOOK.md#wallet-duplicate-payment) ile reddedilir.
3. **Sonra ne yapılacak.** Somut tek bir adım.

Anlamı kod taşır. 403 ya da 429 gibi bir HTTP durumu yalnızca isteğin yolda nasıl sonuçlandığını söyler ve iki farklı ret aynı durumu paylaşabilir. Durumu değil, kodu okuyun.

## Kodlar ve Hata Kitabı

Bir kodun adı asla değiştirilmez ve kod asla farklı bir anlam için yeniden kullanılmaz. [Hata Kitabı](../ERROR_BOOK.md), uygulamanın kendi hata kataloğundan oluşturulur; bu yüzden oradaki kodlar, iletiler ve sonraki adımlar tam olarak uygulamanın gösterdikleridir. Her kodun kendi bölümü vardır: sayfada kodu size verildiği gibi arayın ya da bölüme kitabın adresi, `#` ve alt çizgileri kısa çizgiye çevrilmiş kodla bağlantı verin; örneğin [`wallet_broadcast_failed`](../ERROR_BOOK.md#wallet-broadcast-failed).

En sık görebileceğiniz kodlar:

| Kod | Başlık | Neler zaten olmuş olabilir |
| --- | --- | --- |
| [`permission_denied`](../ERROR_BOOK.md#permission-denied) | İzin reddedildi | Hiçbir şey değiştirilmedi. |
| [`provider_unavailable`](../ERROR_BOOK.md#provider-unavailable) | Sağlayıcıya ulaşılamıyor | Hiçbir şey gönderilmedi. |
| [`provider_exhausted`](../ERROR_BOOK.md#provider-exhausted) | Sağlayıcı sınırına ulaşıldı | İstek sağlayıcıya ulaştı. |
| [`timeout`](../ERROR_BOOK.md#timeout) | Zaman aşımı | Bilinmiyor: yeniden denemeden önce kontrol edin. |
| [`cancelled`](../ERROR_BOOK.md#cancelled) | İptal edildi | Bilinmiyor: bir kısmı zaten çalışmış olabilir. |
| [`credential_failure`](../ERROR_BOOK.md#credential-failure) | Saklı kimlik bilgisi okunamadı | Hiçbir şey gönderilmedi ya da değiştirilmedi. |
| [`unknown`](../ERROR_BOOK.md#unknown) | Beklenmeyen hata | Bilinmiyor: yeniden denemeden önce kontrol edin. |
| [`wallet_quote_mismatch`](../ERROR_BOOK.md#wallet-quote-mismatch) | Onay artık önizlemeyle uyuşmuyor | Hiçbir şey imzalanmadı ya da gönderilmedi. |
| [`wallet_broadcast_failed`](../ERROR_BOOK.md#wallet-broadcast-failed) | Ödeme yayınlanamadı | Bilinmiyor: yeniden denemeden önce ödemenin durumunu kontrol edin. |
| [`wallet_duplicate_payment`](../ERROR_BOOK.md#wallet-duplicate-payment) | Ödeme zaten önerildi veya gönderildi | Yeni hiçbir şey gönderilmedi; önceki ödeme kayıtlı. |

## Hatalar ve diğer sonuçlar

VOOL bunları birbirinden ayırır, ifadeleri de öyle:

* **Bir hata.** Bir şey başarısız oldu. Kod ve ileti neyin başarısız olduğunu ve ne yapılacağını söyler.
* **Sizi bekliyor.** Bir izin isteği ya da bir ödeme onayı açık. Hiçbir şey çalışmadı ve siz yanıtlayana kadar da çalışmayacak. Bkz. [İzinler](../concepts/permissions.md).
* **Bir harcama tavanı ya da fiyat koruması tarafından durduruldu.** Ret, sağlayıcıyla iletişim kurulmadan önce olur ve ileti asla sağlayıcıya sorulduğunu ima etmez. Bkz. [Harcama sınırları ve fiyat korumaları](../guides/spending-limits.md).
* **İptal edildi.** Bitmeden önce siz ya da VOOL tarafından durduruldu. Bir kısmı zaten çalışmış olabilir; bu yüzden yeniden başlatmadan önce neyin değiştiğini kontrol edin.
* **Burada mümkün değil.** Bu derleme ya da bu makine bunu yapamaz ve ileti bunu neyin yapabileceğini söyler: örneğin VOOL'un uçtan uca denetleyemediği bir şemaya sahip bir ödeme teklifi ya da kurulu olmayan isteğe bağlı bir ek.
* **Sonuç bilinmiyor.** Eylem tamamlanmış da olabilir, tamamlanmamış da; örneğin yayını hiç doğrulanmamış bir ödeme. Önce durumunu kontrol edin. VOOL asla körü körüne yeniden ödeme yapmaz.

## Kendi kodları olan hatalar

VOOL'un birkaç bölümü başarısızlıklarını, başarısızlığın olduğu ekranda kendi kısa kodlarıyla açıklar ve bunlar Hata Kitabı'nda yer almaz: ekler (örneğin çok büyük bir dosya ya da yapıştırılan bir dosyada bulunan bir kimlik bilgisi), uygulama güncellemeleri, proje klasörleri, model seçimi, harcama tavanları, kişiler, eklentiler ve beceriler, sesli giriş, saklanan veriler ve hata raporları. Bunların iletileri de aynı şekilde ne olduğunu ve ne yapılacağını söyler. Bkz. [Sohbetler ve ekler](../concepts/chats-and-attachments.md), [Güncellemeler ve geçiş](updates-migration.md) ve [Hata bildirimi ve gizlilik](../trust/bug-reporting.md).

## Tanımadığınız bir kod

Hata Kitabı'nda olmayan bir kod VOOL'un bu sürümünden gelmemiştir. Onu hangi sürümün gösterdiğini kontrol edin, ardından o sürümün [Hata Kitabı](../ERROR_BOOK.md) içinde arayın. Kod bu sürümden geldiyse onu tam olarak yazıldığı gibi bildirin; yanıt bir referans (`fault-` ve on iki karakter) içeriyorsa onu da ekleyin. VOOL'un sınıflandıramadığı bir başarısızlık asla tahmin edilen bir neden olarak değil, [`unknown`](../ERROR_BOOK.md#unknown) olarak gösterilir. Bkz. [Hata bildirimi ve gizlilik](../trust/bug-reporting.md).

## Belirli belirtilerde sorun giderme

Kurulum, model ve platform sorunları için [Sorun giderme](../help/troubleshooting.md), sohbetlerinizi, hafızanızı ve cüzdanınızı geri almak için [Yedekleme ve kurtarma](../help/backup-and-recovery.md) sayfasına bakın.

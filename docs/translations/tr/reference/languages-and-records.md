---
description: Diller, erişilebilirlik, etkinlik geçmişi, makbuzlar ve model yanıtlarının nasıl doğrulandığı.
translation: machine
source_sha256: 2e135f9673ae465cee9db53572ab89c93235805dfea713efd73d7f4244b6b1f5
---

# Diller, erişilebilirlik ve kayıtlar

## Arayüz dilleri

VOOL'un arayüzü birden çok dilde kullanılabilir. Dil seçimi yalnızca bir sunum meselesidir: izin kararları, harcama yetkisi ve hata kodları **yapıları gereği dilden bağımsızdır**; çevrilmiş bir hata iletisi, İngilizce yetkinin verdiği kararı asla genişletemez ya da daraltamaz.

## Erişilebilirlik

* Ana akışlar için klavye kısayolları (bkz. [Klavye kısayolları](shortcuts.md)).
* Belgelerde ve uygulama yüzeylerinde, sistem tercihinizi izleyen ve elle değiştirilebilen açık ve koyu görünüm.
* Hatalar yalnızca renkle değil, metinle belirtilir; yıkıcı eylemler tek bir yanlış tıklamayla değil, her zaman açık bir onayla gerçekleşir.

## Etkinlik

Etkinlik görünümü, VOOL'un yaptıklarının tam geçmişidir: girdileri ve sonuçlarıyla araç çağrıları, verdiğiniz ya da reddettiğiniz onaylar ve makbuzlarıyla ödemeler. Bir koruma tarafından durdurulan bir tur, durmayı ve korumayı gösterir; bir ret bir yokluk değil, kaydedilmiş bir olaydır.

## Makbuzlar

Para ve harcamayla ilgili eylemler **makbuz** yazar: neyin önerildiği, neyin onaylandığı, neyin gönderildiği ve neyin kesinleştiği. Bilinmeyen sonuçlar bilinmiyor olarak kaydedilir; asla başarı ya da başarısızlık gibi anlatılmaz. *"Tekrar denemeden önce durumunu kontrol edin"* önerisini uygulanabilir kılan budur: makbuz, ödemenin gerçekte hangi durumda olduğunu söyler.

## Model doğrulama durumu

VOOL, bir modelin ne **söylediğini** çalışma zamanının neyi **kanıtladığından** ayırır:

* Araç sonuçları yürütme kayıtlarıyla birlikte kaydedilir; bir yanıtın dosyalar, ödemeler ya da veriler hakkındaki iddiaları bu kayıtlara bağlanır ve desteklenmeyen iddialar gösterilmez.
* Makbuzlar bir doğrulama durumu taşır; bir şerit bu derlemede deneysel ya da doğrulanamaz olduğunda durum, kanıt varmış gibi göstermek yerine bunu söyler.

Kavram için [Makbuzlar](../concepts/receipts.md), başarısızlıkların nasıl sınıflandırıldığı için [Hatalar](errors.md) sayfasına bakın.

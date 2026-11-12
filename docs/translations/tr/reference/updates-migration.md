---
description: VOOL'un kendini nasıl güncellediği, verilerinize ne olduğu ve sürüm uyumluluğu.
translation: machine
source_sha256: 3b6a950556f4669f116dfa3a911ffb5e07ad8bcfa04415d68cf5a3d1adf0fe65
---

# Güncellemeler ve geçiş

## Güncellemeler nasıl çalışır

0.7.5'ten itibaren VOOL kendini günceller: "Kendi başına çevrimiçi olma" açıksa bir bildirim gösterir ve yeni sürümü tek dokunuşla kurar. Bir Mac'te 0.7.4 veya 0.7.3'ten güncelleme: "Kendi başına çevrimiçi olma"yı açın (Ayarlar > Gizlilik ve İzinler) ve bir kez "Güncellemeleri kontrol et"e basın. Linux'ta 0.7.4 veya daha eskisinden ya da Mac'te 0.7.2 ve daha eskisinden: 0.7.7'yi indirin ve mevcut sürümünüzün üzerine kurun. Sohbetleriniz ve ayarlarınız uygulamanın dışında, profilinizde durur.

"Kendi başına çevrimiçi olma" açıkken VOOL başlarken ve günde bir kez yeni bir sürüm olup olmadığını kontrol eder ve "Güncellemeleri kontrol et"e basmanız gerekmeden bir bildirim gösterir. Kapalıyken VOOL hiçbir şey indirmez ve başlıkta **Güncelleme denetimi kapalı** yazar.

İmzalı bir güncelleme paketi, otomatik geri almalı ve doğrulanmış bir işlem hattı çalıştırır:

1. **İndir** — imzalı bir manifeste karşı doğrulanır.
2. **Hazırlanmış kurulum** — yeni sürüm eskisinin yanına kurulur.
3. **Geçiş** — verileriniz yeni depo sürümüne taşınır. Geçiş başarısız olursa verileriniz önceki sürüme geri yüklenir ve uygulama onunla yeniden başlar: *"Your data was restored to the previous version."*
4. **Sağlık denetimi** — eski sürüm emekliye ayrılmadan önce yeni sürüm hizmet verebildiğini kanıtlamalıdır.
5. **Yer değiştirme** — atomiktir; önceki sürüm geri alma hedefi olarak saklanır.

Linux'ta tar arşivi kurulumu da aynı şekilde güncellenir. Yeni sürüm çalışan sürümün yanına kurulur ve `current` tek adımda ona geçer. Yeni sürüm sağlıklı değilse VOOL kendiliğinden öncekine geri döner. Elle **Roll back** seçeneği Mac'te ve kaynak koddan kurulumlarda sunulur, ancak Linux tar arşivi kurulumunda henüz sunulmaz.

Herhangi bir aşama başarısız olursa uygulama olduğu gibi bırakılır; hata iletisi aşamayı adlandırır (`verification_failed`, `insufficient_disk_space`, …) ve her aşamanın kendi kurtarma cümlesi vardır.

## Veri uyumluluğu

* Profiliniz, sohbetleriniz, makbuzlarınız ve ayarlarınız uygulama paketinin dışında durur; bu yüzden uygulamayı değiştirmek asla verilerinizi değiştirmez.
* Yerel depo, **sürüm düşürüldüğünde** açılmayı reddeder: daha yeni bir VOOL'un yazdığı bir veritabanı daha eski bir sürüm tarafından sessizce okunmaz; eski sürüm bunu söyler ve reddeder, çünkü bilinmeyen bir depo düzenini tahmin etmek onu bozar. Yeni verileri okumak için daha yeni sürümü yeniden kurun.
* Güncellemeyle kurulan dosyalar ve güncelleme durumunun kendisi profilinizin altında makbuzlarla kaydedilir; böylece "verilerime hangi sürüm dokundu" sorusu sonradan yanıtlanabilir.

## Beta kanalı

Güncel sürüm hattı bir **beta**'dır (bkz. [Sürüm durumu](../trust/release-status.md)). Güncellemeler sabitlenmiş yayıncı anahtarıyla imzalanır ve kurulmadan önce kontrol edilir. macOS uygulamasının kendisi ad-hoc imzalıdır ve Apple tarafından onaylanmamıştır (noter tasdikli değildir); bu yüzden macOS yeni bir sürümün ilk açılışını onaylamanızı isteyebilir.

---
description: VOOL'un hangi aşamada olduğu, açıkça.
translation: machine
source_sha256: bf79dfc262c1aca9c489c8474ad2de5fa633b0b60a515f2835f52954c66ad73f
---

# Sürüm durumu

Bu sayfa, sürüm durumu için güncel tutulan yetkili kaynaktır. Diğer belgeler sürümle ilgili bilgilere geçerken değinir; bu sayfayla çeliştiklerinde bu sayfa geçerlidir.

## Güncel aşama

VOOL **beta** aşamasındadır. Güncel sürüm [**v0.7.7-beta**](https://github.com/Parad0x-Labs/vool/releases/tag/v0.7.7-beta) olup `main` dalından `8af687c` üzerinde derlenmiş bir GitHub sürümüdür. Üç platform için uygulama indirmeleri vardır ve her birinin yayımlanmış bir SHA-256 sağlama toplamı bulunur. Önyükleme betiğiyle kaynak koddan kurulum macOS, Linux ve Windows'ta desteklenmeye devam eder; bkz. [Kurulum](../getting-started/install.md).

| İndirme | Kimin için |
| --- | --- |
| `VOOL-0.7.7-beta-macos-arm64.dmg` | Apple Silicon Mac'ler, macOS 14 veya daha yenisi |
| `VOOL-0.7.7-beta-macos-x86_64.dmg` | Intel Mac'ler, macOS 14 veya daha yenisi |
| `vool-0.7.7-beta-linux-x86_64.tar.gz` | Linux x86_64, Ubuntu 22.04 ve 24.04'te test edildi |

Bu deponun sürüm kayıtları GitHub kanalını kapsar. Diğer indirme kanalları ayrı olarak işletilir; her indirmeyi GitHub sürümünde yayımlanan sağlama toplamıyla doğrulayın.

| | Durum |
| --- | --- |
| Herkese açık indirmeler | v0.7.7-beta GitHub sürümünde macOS Apple Silicon DMG, macOS Intel DMG, Linux x86_64 tar arşivi |
| Yayımlanan sağlama toplamları | Her indirme için SHA-256, sürümde |
| Kod imzalama | macOS uygulamaları Developer ID ile değil, ad-hoc imzalıdır. İmzalama bu betadan sonra eklenir ve betayı engellemez |
| macOS onayı (noter tasdiki) | Apple tarafından onaylanmamış (noter tasdikli değil). macOS ilk açılışı onaylamanızı ister (aşağıya bakın) |
| Windows | Bu sürümde Windows indirmesi yok; Windows'ta kurulum kaynak koddan yapılır ve deneyseldir |
| Güncellemeler | 0.7.5'ten itibaren VOOL kendini günceller: "Kendi başına çevrimiçi olma" açıksa bir bildirim gösterir ve yeni sürümü tek dokunuşla kurar. Bir Mac'te 0.7.4 veya 0.7.3'ten güncelleme: "Kendi başına çevrimiçi olma"yı açın (Ayarlar > Gizlilik ve İzinler) ve bir kez "Güncellemeleri kontrol et"e basın. Linux'ta ya da 0.7.2, 0.7.1 veya 0.7.0'dan: 0.7.7'yi indirin ve mevcut sürümünüzün üzerine kurun. Sohbetleriniz ve ayarlarınız uygulamanın dışında, profilinizde durur. Bkz. [Güncellemeler ve geçiş](../reference/updates-migration.md) |
| API kararlılığı | Derlemeler arasında değişiyor |

## Bunun sizin için anlamı

* Bir Mac uygulamasının ilk açılışında macOS, uygulama Apple tarafından onaylanmadığı (noter tasdikli olmadığı) için onu açmayı reddeder. Bu aşamada bu beklenen bir durumdur, hasarlı bir indirmenin işareti değildir. Sağlama toplamını doğrulayın, ardından **Sistem Ayarları → Gizlilik ve Güvenlik**'i açın ve **Yine de Aç**'ı tıklayın. macOS 14'te bunun yerine Kontrol tuşuna basarak uygulamayı tıklayıp **Aç**'ı seçebilirsiniz.
* İşlemcinize uyan Mac indirmesini seçin. Apple Silicon uygulaması Intel Mac'te çalışmaz ve açılışta bunu söyler.
* Yapılandırma biçimleri derlemeler arasında değişebilir.
* Kripto özellikleri siz açana kadar kapalıdır.

## Beta ve pilot sınırlamaları, açıkça

| Yetenek | Bu derlemedeki durumu |
| --- | --- |
| Kripto ve cüzdan | Varsayılan olarak kapalı. Açtığınızda VOOL mainnet kullanır; test ağları Developer options içindedir. Her ödeme sınırları, onayınız ve bir makbuzla VOOL'un cüzdanından geçer. Bkz. [Cüzdan](../guides/wallet.md). |
| Mainnet transferleri | Bu cihazda oluşturulmuş bir VOOL Cüzdanından gönderilen yerel paralar ile Solana, Ethereum, BNB Smart Chain ve Arbitrum One'da USDT. Solana ve Base'de USDC yalnızca ödeme kanalları içindir. Bağlı ya da yalnızca izlenen bir hesap ödeme alabilir ve bakiyesini gösterebilir ama VOOL'dan gönderim yapamaz. |
| x402 ile ödeme | Base ya da Base Sepolia üzerinde USDC ile x402 ödemesi isteyen web siteleri Crypto Pilot cüzdanınızdan ödenebilir: sınırlarınız içinde, PIN'inizle tek bir onay. Ağ ücretini web sitesinin ödeme hizmeti karşılar. **Ayarlar → Kripto → Payment channels**, cüzdanın bir web sitesine ödeme yapabileceği her yol için bir açma/kapama anahtarı içerir; kapalı bir kanal herhangi bir şey imzalanmadan önce reddeder. Bununla henüz canlı bir mainnet ödemesi yapılmadı. Solana pay-kit ile ödeme indirmelere dahil değildir; bu kanal kullanılamaz olarak görünür. |
| X | Herhangi bir sohbetten: gönderileri ve hesapları, Spaces'i, gündemleri ve DM'leri arayın ve bir token hakkındaki geçmiş call'lara bakın. Hesabınız üzerinde işlem yapan her şey onayınızı bekler. |
| Diktleme | Bu beta derlemesinde geçici olarak kullanılamıyor; bir güncellemeyle geri dönecek. |
| Bulut "burst" soru modu | Çağrı başına istem bu derlemede henüz bağlanmadı. |
| Yerel modeller | Yerel bir modelin yanıt verebilmesi için önce **Ayarlar → Modeller ve sağlayıcılar → Yerel modeller**'i açın ve o model için bir kez **Certify**'a basın. |
| Intel Mac'ler | Yerel modeller işlemcide çalışır ve yavaştır. Sertifikalama daha yavaş işlemcileri hesaba katar, ancak hafızaya dayanan uzun bir yanıt zaman aşımına uğrayabilir; bu yüzden şimdilik orada bir bulut modeli kullanın. |
| Özel sohbet | "özel sohbet", "bunu kaydetmeyin" ya da "aramızda kalsın" deyin; o sohbetten hiçbir şey kaydedilmez. Komutlar uygulamanın 17 dilinin hepsinde çalışır. |
| Unutma | Unutma, VOOL'un hafızasını ve eşleşen çalıştırma geçmişini temizler, böylece öğe yeniden hatırlanmaz ya da listelenmez; ayrıca unutulan sözcükleri VOOL'un yerel hafıza dosyasından ve arama dizininden siler. VOOL, unutmasını istediğiniz sözcükleri, onları reddetmeyi sürdürebilmek için bilgisayarınızda kısa bir listede tutar. Bir sohbette bir şeyi unutturduktan sonra VOOL aynı bilgiyi o sohbette yeniden kabul etmez; bunu ona yeniden söylemek için yeni bir sohbet başlatın. |
| Taranmış PDF'ler | Mac'in yerleşik metin tanıma özelliğiyle okunur, bu yüzden yalnızca Mac'lerde. |
| Masaüstü evcil hayvanı | Evcil hayvan VOOL penceresinde yaşar. Onu pencerenin dışına sürüklemek masaüstüne taşımaz. |
| Beceri Kodeksi | "Beceri Kodeksine göz at", tam olarak sizin VOOL sürümünüz için listelenen becerileri gösterir ve bir beceriyi kurulmadan önce incelersiniz. "Beceri Kodeksinde yayınla", bilgisayarınızda yaptığınız bir beceriyi kontrol eder, ardından Beceri Kodeksi web sitesini beceri doldurulmuş olarak açar; orada oturum açar ve Send'e basarsınız, VOOL kendisi hiçbir şey göndermez. Yalnızca becerinin metni gider, bu yüzden ek dosyaları olan bir beceri henüz paylaşılamaz. |

macOS için Developer ID imzalama ve Apple onayı (noter tasdiki) ile Linux için imzalı sağlama toplamları, sürüm yolundaki sonraki adımlardır; bugün hiçbiri iddia edilmez.

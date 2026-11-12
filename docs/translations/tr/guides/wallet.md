---
description: İsteğe bağlı Crypto Pilot cüzdanı: bu derlemenin gerçekte sunduğu haliyle ağlar, x402 ödemeleri, DNA ücretleri ve kurtarma.
translation: machine
source_sha256: 46121a5ed26be175ddd3c66b269df356e074adbb57c8e9806461b80197613cc2
---

# Cüzdan

Cüzdan **isteğe bağlıdır ve varsayılan olarak kapalıdır**. VOOL'un, istek başına ücret alan yapay zekâ hizmetleri için az miktarda kripto tutabilmesi amacıyla vardır. VOOL'u kullanmak için onunla ilgili hiçbir şey gerekmez.

Bu sayfa, güncel derlemenin gerçekte neyi desteklediğini anlatır. Cüzdan **pilot** aşamasındadır: yetenekler şerit şerit eklenir ve aşağıdaki her şerit tam olarak ne yaptığını söyler.

## Ağ ortamları

Kripto açıkken VOOL **Mainnet** üzerinde çalışır: Solana, Robinhood Chain, Base, Ethereum, BNB ve Arbitrum One. Normal kurulum sizden asla bir ağ ortamı seçmenizi istemez.

* **Mainnet** — eski test ağı hesaplarıyla yükseltilmiş kurulumlar dahil, her kurulum için varsayılan. Her harcama tavanı, onay ve makbuz aşağıda anlatıldığı gibi geçerlidir.
* **Test networks** — Solana Devnet, Robinhood Chain Testnet ve diğer test şeritleri. Yalnızca geliştiriciler için: **Ayarlar → Kripto → Developer options** içinden geçiş yapın.

Hesaplar, bakiyeler ve makbuzlar tek bir ortama aittir ve ortamlar arasında asla taşınmaz; diğer ortamdaki hesaplar kendi ağlarında listelenmeye devam eder. Diğer ortamdaki bir ağ için yapılan bir istek, hiçbir şey oluşturulmadan, önerilmeden ya da imzalanmadan reddedilir.

{% hint style="warning" %}
Önceki belgeler bu derlemede mainnet'in imkânsız olduğunu söylüyordu. Bu, pilottan önce doğruydu, artık değil: mainnet satırları artık var. Doğru kalan şey daha dardır; mainnet'te tam olarak hangi şeritlerin bulunduğu için aşağıdaki transferler ve ödemeler bölümlerine bakın.
{% endhint %}

## Hangi şeritte ne taşınır

* **Transferler yerel paraları taşır** (örneğin Solana'da SOL; Base, Ethereum ya da Arbitrum One'da ETH), ayrıca **Solana, Ethereum, BNB Smart Chain ve Arbitrum One'da USDT**. Zincir, kayıtlı her kişi adresinin ve her onayın bir parçasıdır. Token transferleri ücretler için o zincirin yerel parasına da ihtiyaç duyar; fiyat teklifi iki tutarı da sınırlarınız içinde gösterir.
* **Solana ve Base'de USDC yalnızca ödeme kanalları için kullanılabilir.** Sıradan gönderimler için sunulmaz. Cüzdanın ana bakiye ekranı tam bir token portföyünü değil, yerel paraları gösterir.
* Her transfer önizlenir (bir **fiyat teklifi**: tutar, ücret tavanı, alıcı, ağ) ve tam olarak bu önizlemeye bağlı açık onayınızı gerektirir. Herhangi bir şeyi değiştirirseniz onay geçersiz olur; hiçbir şey imzalanmaz.
* Birden fazla kişiyle ya da cüzdan adresiyle eşleşen bir ad, sizden seçim yapmanızı ister. Tam adresi ve zinciri gözden geçirin. Değişen bir adres ya da yeniden adlandırılan bir kişi eski incelemeyi geçersiz kılar.
* Mainnet'te yalnızca bu cihazda oluşturulmuş bir VOOL Cüzdanı gönderim yapar. Bağladığınız dış bir cüzdan ya da yalnızca izlenen bir adres ödeme alabilir ve bakiyesini gösterebilir; ondan yapılacak bir gönderim ya da ödeme kanalı ödemesi herhangi bir onay istenmeden önce reddedilir ve hiçbir şey imzalanmaz.
* Etkinlik, VOOL üzerinden yapılan ödemeleri ve makbuzlarını kaydeder. Tam bir gelen ödeme geçmişi değildir. Bu cüzdan herhangi bir takas (swap) ya da köprü (bridge) işlemi sunmaz.

## Bir ödeme nasıl onaylanır

1. VOOL, tam tutarları ve bir ücret tavanını içeren bir öneri hazırlar.
2. Siz onu PIN'iniz/parolanızla (ya da kullanılabildiği yerde cihaz kimlik doğrulamasıyla) onaylarsınız.
3. Ödeme imzalanır ve gönderilir, bir makbuz kaydedilir.

Onaylar öneriler arasında aktarılmaz. Yanlış bir PIN hiçbir şey imzalamaz. Çok fazla yanlış deneme cüzdanı bir süreliğine kilitler; bu bilerek yapılır ve beklenecek süre belirtilir.

## x402 otomatik ödemeleri

x402, bazı web sitelerinin ve yapay zekâ hizmetlerinin kullandığı, istek başına bir ödeme protokolüdür. **Base üzerinde USDC ile x402 ödemesi isteyen web siteleri** (ya da Developer options içinde Base Sepolia) Crypto Pilot cüzdanınızdan ödenebilir: sınırlarınız içinde, PIN'inizle tek bir onay. Ağ ücretini web sitesinin ödeme hizmeti karşılar. **Bununla henüz canlı bir mainnet ödemesi yapılmadı.** Diğer test ağları (Solana Devnet, Ethereum Sepolia ve BNB testnet) Developer options içinde kalır. **Ayarlar → Kripto → Payment channels**, cüzdanın bir web sitesine ödeme yapabileceği her yolu, her birinin kendi açma/kapama anahtarıyla listeler; kapalı bir kanal herhangi bir şey imzalanmadan önce reddeder. Solana pay-kit ile ödeme indirmelere dahil değildir, bu yüzden bu kanal kullanılamaz olarak görünür. **Model pazar yeri:** Ayarlar'daki model pazar yerindeki çağrı başına ödeme seçeneği, Solana Mainnet Crypto Pilot cüzdanından USDC ya da SOL ile ödeme yapabilir. Her çağrı, cüzdan sınırlarınız içinde onayınızı bekler ve %0,1'lik VOOL ücretini gösterir. Bu yol henüz gerçek bir zincirde test edilmedi. Ücretli bir kaynak x402 kullandığında:

* Ödemeler aynı cüzdan tavanlarından ve onay akışından geçer.
* Otomatik ödemelerin ödeme başına bir tavanı vardır; tavanın üzerindeki bir kaynak **ödenmez** (tavanı bilinçli olarak yükseltebilir ya da açık bir öneriyle ödeyebilirsiniz).
* Bir ödeme tamamlanamadıysa sonuç **bilinmiyor** olarak bildirilir; yeniden denemeden önce durumunu kontrol edin. VOOL sizi körü körüne yinelenen bir ödemeye davet etmez.

## DNA hizmet ücreti

Model pazar yerinin çağrı başına x402 yolu, kabul edilen her sağlayıcı ödemesinden **%0,1 (10 baz puan)** hizmet ücreti alır; bu ücret ödemeden ve ağ ücretinden ayrı gösterilir. Başka hiçbir şeyde bu ücret alınmaz: sıradan gönderimler, cüzdan oluşturma, yerel çıkarım ve kendi API anahtarınızla (BYOK) yapılan çağrılar **hiçbir** hizmet ücreti taşımaz. Biriken ücretler ekonomik biçimde toplanır (daha sonraki bir yerel para ödemesiyle, tek bir onay altında, yalnızca toplama maliyeti sınırlıysa); asla sürpriz, ayrı bir ücretle toplanmaz.

## Kurtarma

* **Kurtarma ifadesi tam olarak bir kez gösterilir**, oluşturma sırasında.
* Bir pilot cüzdanın yedek anahtarı da aynı şekilde kurulum sırasında bir kez verilir ve daha sonra dışa aktarılamaz. Kaydetmediyseniz kurulumu iptal edin ve o adrese fon göndermeyin.
* Crypto Pilot'tan önce oluşturulmuş eski bir VOOL Cüzdanı, başka bir cüzdan uygulamasına aktarılmak üzere özel anahtarını bir kez dışa aktarabilir. Dışa aktarma yalnızca Mac'inizin kendi oturum açma yöntemiyle (Touch ID ya da Mac parolanız) açılır, asla cüzdan PIN'iyle açılmaz ve anahtar saklanmadan bir kez gösterilir.
* Bir cüzdanı mı kaybettiniz? Oluşturma sırasında not ettiğiniz ifadeden kurtarın. Tam olarak bu cüzdana uymayan bir kurtarma kanıtı, herhangi bir yazma işleminden önce reddedilir.

---
translation: machine
source_sha256: 671f147e5d9649b6c2b7c2d391ea085120e783a8ace29f92ac8c3d5ecb4407eb
---
# Becerileri keşfetme ve Eyebrow kontrolleri

Beceriler ve Eklentiler sayfasında dört sekme vardır: **Göz at**, **Beceri Kodeksi**, **Eklentilerim** ve **Güvenlik**. Göz at önce VOOL'un kendi becerilerini listeler (uygulamayla birlikte gelen, yalnızca metinden oluşan 53 VOOL becerisi dahil), ardından incelenmiş katalog eklentilerini listeler ve herkese açık bir GitHub deposunu sabitlenmiş bir sürümde arayabilir. Beceri Kodeksi, editör seçimlerinin ve öne çıkan becerilerin üstünde, önce VOOL ekibinin becerilerini listeler. Arama büyük/küçük harfi ve yaygın ayırıcıları yok sayar; kategori ve sıralama denetimleri listeyi daraltır. Kaynak, lisans, sürüm ve gereksinimler indirmeden önce görünür.

## İlk kullanım

1. Güvenlik sekmesinde bir Eyebrow API anahtarı kaydedin; bu, Ayarlar → API anahtarları altında da yapılabilir. Anahtar, mevcut kimlik bilgisi deposuna aittir. Kaydetmek bağlantıyı test etmez ve hiçbir şeyi taramaz; "Bağlantıyı test et" Eyebrow ile açıkça iletişim kurar.
2. Göz at sekmesinde bir beceri seçin ve onun herkese açık içeriğinin indirilmesini ve bu içeriğin Eyebrow'a gönderilmesini onaylayın. Bu, planınıza göre hizmetin tarama kotasından düşer. Otomatik yeniden deneme yapılmaz.
3. Dönen raporu okuyun. Başarılı bir HTTP yanıtı tek başına temiz bir sonuç değildir. Bu ilk katalog kuruluma yalnızca eksiksiz bir `pass` raporuyla, hiç bulgu ve hiç ilke ihlali olmadığında izin verir. "Reddet", hazırlanan indirmeyi kurmadan atar.
4. Kurulumu ayrıca kabul edin. VOOL lisansı korur, mevcut eklenti yaşam döngüsü üzerinden kurar, incelenmiş paketin tam olarak kendisini doğrular ve beceriyi etkinleştirir. Becerinin önerebileceği her eylemi yine mevcut izinler yönetir.

Tarama, bir firmanın belirli baytlar hakkındaki değerlendirmesidir; güvenlik, uyumluluk ya da yanıt kalitesi garantisi değildir. Kataloğa alınmak, yayıncının sertifikalandığı anlamına gelmez.

## Sınırlar

İçerik yalnızca 40 karakterlik sabit bir sürümdeki herkese açık GitHub depolarından, SHA-256 ile sabitlenmiş beceri ve lisans dosyalarıyla gelir. Bu sürümde özel depo, arşiv açma, kurulum betiği ya da yürütülebilir/MCP eklenti içe aktarıcısı yoktur. Eyebrow'a yalnızca bağımsız SKILL.md dosyası gönderilir; yerel sohbetler, çalışma alanı dosyaları, kimlik bilgileri ve lisans içerikleri taramaya gönderilmez.

İndirmeler inceleme ve kabule kadar etkisiz kalır. Yerel olarak kimliği doğrulanmış makbuzlar raporu sabitlenmiş içeriğe bağlar. Kurulum; süresi dolmuş incelemeleri (24 saat), değiştirilmiş hazırlanmış dosyaları ve zaten var olan hedef dizinleri reddeder. Çalışma zamanı, yönetilen bir beceriyi model bağlamına eklemeden önce yaşam döngüsü durumunu ve incelenmiş baytları denetler. Eklenen dosyalar, sembolik bağlantılar ya da değişen içerik beceriyi kullanılamaz hale getirir. Yönergeler bütün olarak eklenir ya da istem bütçesi yetersiz olduğu için dışarıda bırakıldığı açıkça kaydedilir; taranan talimatlar sessizce kırpılmaz.

Bu denetimler, işletim sistemi kullanıcısı olarak zaten çalışmakta olan kötü amaçlı kodu yalıtmaz. Önceden elle kurulmuş eklentileri kapsamaz. Bir tarama makbuzu yeni bir yürütme izni ya da cüzdan yetkisi vermez.

## API ve hata işleme

`GET /api/addons`, bağlantı testi ya da tarama yapmadan katalog ve yerel durum bilgisini döndürür. `POST /api/addons`; `save_key`, `remove_key`, `test_key`, `scan`, `install` ve `reject` işlemlerini destekler. Değişiklik yapan istekler loopback, JSON ve aynı kaynaklı tarayıcı istekleri gerektirir; yalnızca işleme özgü alanlar kabul edilir. Çağıranın verdiği kaynak URL'leri ve yollar reddedilir.

İstemci, Eyebrow v1'in `/v1/scan` adresindeki satır içi beceri kaynağı isteğini bearer kimlik doğrulaması, sabit bir HTTPS kaynağı, yönlendirme olmadan, sınırlı yanıt boyutuyla ve otomatik yeniden deneme olmadan kullanır. Açık bağlantı testi için `/v1/version` kullanılır. API başvurusu: https://eyebrow.cc/api/reference .

Türü belli tipik hatalar şunlardır:

| Kod | Anlamı / kurtarma |
| --- | --- |
| `eyebrow_key_missing` / `eyebrow_unauthorized` | Anahtarı kaydedin ya da değiştirin. |
| `eyebrow_quota` | Yeniden denemeden önce hizmet kotasını gözden geçirin. |
| `eyebrow_timeout` / `eyebrow_unreachable` | Otomatik yeniden deneme yoktur; zaman aşımına uğrayan bir tarama yine de kotadan düşebilir. |
| `eyebrow_invalid_report` / `eyebrow_no_coverage` | Kanıt eksiktir; hiçbir şey etkinleştirilmez. |
| `addon_source_changed` / `addon_changed` | Sabitlenmiş ya da incelenmiş baytlar farklı; bunları etkinleştirmeyin. |
| `review_untrusted` / `review_expired` | Kurulumdan önce yeni bir inceleme alın. |
| `addon_already_installed` | Var olan dosyalar korundu. Otomatik değiştirme uygulanmamıştır. |

## Güncel sınırlamalar ve doğrulama

Var olan, değişmiş bir paket için otomatik güncelleme, rastgele katalog içe aktarıcısı ya da yerinde onarma/yeniden kurma iş akışı yoktur. Uygulama yeniden başladıktan sonra bekleyen incelemeyi kurtarma arayüzde sunulmaz. Yeni arayüz metinleri şu anda İngilizce yedek metinle gelir; eksiksiz çeviri iddia edilmez.

Otomatik testler gerçek yaşam döngüsünü, beceri yüklemeyi, API işleyicilerini ve sunulan tarayıcı arayüzünü, açıkça taklit edilmiş herkese açık indirmeler ve Eyebrow yanıtlarıyla çalıştırır. Bunlar Eyebrow'un canlı kullanılabilirliğini ya da model yetkinliğini değil, çalışma zamanı ve arayüz davranışını kanıtlar. Gerçek bir anahtarla yapılan bir tarama ve yerel paketlenmiş uygulamanın kabulü sürüm denetimleri olarak kalır. Bu testleri çalıştırmak için herhangi bir anahtar ya da ücretli hizmet çağrısı gerekmez.

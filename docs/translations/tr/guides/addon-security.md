---
translation: machine
source_sha256: 4596ad3e82d6936445beafa8e30a334325d70a8a0f1ee052eda3c43ae5118244
---
# Eklenti denetimleri

Eklentiler; göz atmayı, incelemeyi ve kurmayı birbirinden ayrı tutar. Bir tarama belirli baytları tanımlar; bir güvenlik garantisi, yayıncı onayı, çalışma zamanı izni ya da cüzdan onayı değildir.

Göz at önce VOOL'un kendi becerilerini listeler; bunlar arasında uygulamayla birlikte gelen 75 VOOL becerisi vardır (bunların 22'si VOOL araçlarını kullanır ve bu araçlar yalnızca VOOL'un zaten sunduklarını daraltır). Ardından katalogdaki incelenmiş eklentileri listeler. Beceri Kodeksi, editörün seçimlerinin ve öne çıkan becerilerin üstünde, önce VOOL ekibinin becerilerini listeler.

## VOOL'un yaptığı beceriler: yerel denetim

VOOL'un yaptığı 75 beceri uygulamanın içinde gelir; her biri VOOL'un kendi dizininde SHA-256 ile sabitlenmiştir ve VOOL yayımlandığında incelenir. Bunlar için **Bu beceriyi ekle** yalnızca VOOL'un yerel denetimini çalıştırır: anahtar gerekmez, tarama hakkı harcanmaz ve ağ isteği yapılmaz.

1. VOOL beceriyi ve lisansı uygulamanın kendi kopyasından okur ve SHA-256 değerleri sabitlenen değerle eşleşmedikçe onları reddeder (*This VOOL skill's file doesn't match the copy that came with VOOL, so it was not added.*).
2. VOOL'un yerel denetimi beceriyi riskli talimatlar için okur ve sonucu gösterir. Temiz bir sonuç **Ekle ve aç** ile eklenebilir; denetimin işaretlediği her şey, tam olarak o sürüm için ayrı ve açık onayınızı gerektirir. **Reddet** onu atar.
3. Kurulum ardından diğer becerilerle aynı yaşam döngüsünü izler: tam olarak bu dosyalar kurulur, doğrulanır ve etkinleştirilir. Kurulu beceri her yüklendiğinde sabitlenmiş SHA-256 değerine göre denetlenir; değişmiş bir dosya beceriyi kullanılamaz hale getirir.

Yerel denetim sabit baytların statik bir okumasıdır; güvenlik ya da yanıt kalitesi garantisi değildir. Başka yapımcıların becerileri asla bu yoldan geçmez.

## Herkese açık beceriler

Sabit bir revizyondaki herkese açık bir GitHub becerisini seçin ve içeriğinin indirilmesini onaylayın. VOOL onu bu cihazda `core/local_scan.py` ile denetler. Etkin bir tarama eklentisi yoksa sayfa, denetimlerin bu cihazda kaldığını açıklar. Bu yol için tarama anahtarı gerekmez.

Etkin bir tarama eklentisi de, bir tarama istendiğinde herkese açık becerinin içeriğini alır. Bu, onun hizmet hakkını tüketebilir. Yerel sohbetler, özel çalışma alanı dosyaları ve diğer kimlik bilgileri gönderilmez. Bir anahtarı kaydetmek ve Eklentiler'i açmak bağlantı testi ya da tarama isteği yapmaz. Otomatik istek yinelemesi yoktur.

Bulguları okuyun ve kurulumu ayrıca kabul edin. Rapor doğrulaması tam kapsam ve geçerli bulgular gerektirir. Geçen bir raporda bulgu ya da politika ihlali yoktur. Ayrıca sunulan bir risk kabulü incelenen sürüme bağlanır; raporu asla değiştirmez ve daha fazla erişim vermez. Süresi dolmuş incelemeler, değişmiş hazırlık baytları, bağlantılar ve çakışan hedef klasörler reddedilir. Makbuzlar kurulu dağıtımın tamamını bağlar. Değişen dosyalar çalışma zamanı kullanılabilirliğini geri çeker. Beceri yönergeleri bütün olarak eklenir ya da istem bütçesine sığmadığında açıkça dışarıda bırakılır.

## İsteğe bağlı tarama araçları

Uzak bir tarama aracı isteğe bağlıdır. Hiçbir tarama eklentisi etkin değilken bir becerinin kurulup kurulamayacağına VOOL'un bu cihazdaki kendi denetimi karar verir; önceki sürümler bunun yerine uzak bir tarama gerektiriyordu.

Bir tane eklemek için bir tarama eklentisini yapımcısından Eklentiler bölümü aracılığıyla kurun, kendi anahtarınızı kaydedin, erişimi inceleyin ve etkinleştirin. Eklentinin bildirimi, açık bir bağlantı testi için bir yol ve istenen taramalar için bir yol adlandırır; bu istekleri ortak yerleşik eklenti taşıması yapar. VOOL'un yerleşik bir tarama istemcisi ya da merkezi bir tarama anahtarı ayarı yoktur ve kendine ait bir tarama eklentisi sunmaz. Eski bir VOOL'dan kalan kayıtlı bir Eyebrow anahtarı bu bilgisayarda kalır ve kullanılmaz. `vool-plugin-eyebrow` ve `vool-plugin-usepod` kimlikleri ayrılmıştır; bunlardan birini sahiplenen başka bir paket inceleme öncesinde reddedilir.

VOOL'un bu cihazdaki kendi denetimi her zaman çalışır. Bir tarama eklentisinin raporu onun yanına eklenir: ikisinden birinin bir sorun bulması kurulumu engeller ve bir eklenti raporu denetlenen içeriği tam olarak adlandırmalıdır. Bir tarama eklentisi bir kurulumu asla tek başına onaylayamaz.

Anahtarı eksik ya da reddedilmiş, etkin bir tarama aracı açıkça başarısız olur. VOOL istenen o uzak taramayı asla sessizce geçen bir yerel sonuçla değiştirmez. Zaman aşımları, hizmet hataları, yönlendirmeler, aşırı büyük yanıtlar ve eksik raporlar kurulumu kabul edilmemiş durumda bırakır. Zaman aşımına uğramış bir istek yine de hizmet hakkını tüketmiş olabilir.

## Yürütülebilir paketler ve mağaza incelemesi

Eklentiler, yalnızca metin içeren beceri içe aktarıcısından ayrı olarak, yerel bir klasörün ya da sabitlenmiş herkese açık bir GitHub paketinin eksiksiz incelemesini kullanır. Kurulum hiçbir paket kodunu çalıştırmaz ve paketi devre dışı tutar. Mağaza eklentileri ayrıca mevcut imzalı makbuzu ve tam dağıtım özetini gerektirir. Etkinleştirme; araçları, etkileri, tam ana makineleri, kimlik bilgisi yuvalarını ve paketle gelen sunucuları kapsayan ayrı bir erişim sayfası gerektirir.

Yürütülebilir kod çağrıldığında yalnızca kendi bildirdiği anahtarları alır. Başka bir eklentinin yuvalarını, sahibin ücretli model anahtarlarını ya da cüzdan imzalama, limit, onay veya makbuz yetkisini okuyamaz. Çıktısı güvenilmez olarak kalır. Değişen baytlar onayı geri çeker; devre dışı bırakma çağrıları durdurur. Desteklenmeyen kısıtlama yürütmeyi reddeder. Çalışma zamanı sınırları için bkz. [Eklenti yazma](../../../guides/plugin-authoring.md).

VOOL eklenti kayıtlarını okuyamazsa, kaydı olmayan paketler, VOOL ile gelenler dışında, beceri sunmayı bırakır ve bozuk dosya yenisinin yanında tutulur. Açma ve kapama seçimlerinizi okuyamazsa, siz birini yeniden açana kadar her eklenti kapalı sayılır.

## Yerel API ve taşıma

`GET /api/addons` envanteri okur. `POST /api/addons` adresindeki değişiklikler sahibe ait yerel erişim, JSON, aynı kaynaklı tarayıcı istekleri ve eyleme özgü alanlar gerektirir. Eklenti açma/kapama ve yaşam döngüsü kapıları (`POST /api/plugins/enable`, `POST /api/plugins/lifecycle`) aynı JSON ve aynı kaynak kuralını uygular. Herkese açık kaynak indirmeleri tam revizyonlar ve boyutu sınırlı, doğrulanmış bloblar kullanır. Yerleşik hizmetler bildirilmiş herkese açık HTTPS ana makineleri gerektirir, TLS'yi doğrular, çözümlenen herkese açık adresleri sabitler, yönlendirmeleri reddeder, yanıtları sınırlar ve eklenti devre dışı bırakıldığında durur.

Yerel sahte HTTPS hizmetleri, ücretli çağrılar olmadan taşımayı ve hata işlemeyi doğrulayabilir. Bunlar canlı hizmet güvenilirliğini, faturalandırmayı, model yetkinliğini ya da başka uygulamaların içindeki kurulumu kanıtlamaz. Bunlar ayrı doğrulama gerektirir.

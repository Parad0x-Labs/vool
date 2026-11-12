---
description: VOOL'u beceriler ve eklentilerle genişletin.
translation: machine
source_sha256: a86860b8624e3c97c81e813ec92e3998c687a87d7bc43b7960c489cdcd309196
---

# Beceriler ve eklentiler

**Beceri**, tekrarlanan bir iş için paketlenmiş bir talimatlar kümesidir. **Eklenti** ise çalışma zamanına yeni araçlar ekler.

## Birlikte gelen ve haricen kurulan

VOOL bir **yerleşik beceri kitaplığı** (uygulamanın kendisiyle gelen beceriler) ve güncel derlemelerde **VOOL Database** eklentisiyle birlikte gelir. Kendi beceri ve eklentilerinizi de kurabilirsiniz.

| | Birlikte gelen (VOOL ile gelir) | Haricen kurulan |
| --- | --- | --- |
| Nerede durur | Uygulamanın içinde | Eklenti klasörünüzde (Ayarlar'da belirlenir) |
| Güncellemeler | Uygulamayla birlikte | Paketi güncellediğinizde |
| Devre dışı bırakılabilir mi | Evet — tek tek | Evet — tek tek |
| İzin verir mi | **Hayır** | **Hayır** |

İki kural her iki kaynak için de geçerlidir:

* **Beceriler öğretir; asla izin vermez.** Bir becerinin bildirdiği araçlar bir *daraltma* sinyalidir (iş akışının araç bütçesi), asla herhangi bir şeye izin değildir. İzin denetleyicisi becerileri okumaz.
* **Tek envanter.** Konsolun gösterdiği katalog ile çalışma zamanının yüklediği paketler aynı listedir. Bir becerinin bir listede görünmesi yüklenebileceğinin kanıtı değildir: her girdi kendi canlı durumunu (kullanılabilir, devre dışı, ön koşul eksik, geçersiz sözleşme) ve nedenini taşır.

## Beceri bulma

Beceriler ve Eklentiler önce VOOL'un kendi becerilerini listeler. **Göz at**, 53 hazır VOOL becerisiyle gelir. Bunlar yalnızca metindir: her biri uygulamayla birlikte gelen bir talimatlar kümesidir, bu yüzden Göz at onları çevrimiçi bir arama yapmadan listeler. VOOL araçlarını kullanan VOOL becerileri **Beceri Kodeksi** sekmesinde listelenir; orada VOOL ekibinin becerileri önce gelir. Bir beceriyi etkinleştirmeden önce inceleyin.

## Kullanılabilirlik durumları

Bir beceri, türü belli bir nedenle kullanılamaz olarak görünebilir: eksik bir ön koşul (örneğin veritabanı becerisi, veritabanı eklentisinin etkin olmasını gerektirir), bu makinede kullanılamayan bir yetenek, geçersiz bir sözleşme ya da sizin devre dışı bırakmanız. Nedenler her girdi için ayrı gösterilir; devre dışı bırakma girdi başınadır ve yeniden başlatmalardan sonra da korunur.

## Yinelenen kimlikler

İki paket aynı eklenti kimliğini bildirirse (örneğin birlikte gelen bir paket ve kurulmuş bir kopya), birlikte gelen paket **deterministik olarak** tutulur ve bırakılan kopya katalogda bildirilir; asla sessizce gölgelenmez.

## Kendi beceri ve eklentilerinizi kurma

Uygulama içi katalogdan kurun ya da VOOL'u size ait bir eklenti klasörüne yöneltin. Paketler, araçlarını bildiren bir manifest (`plugin.json`) taşır; izin bildirimleri, bildirdiği davranış sınıfıyla eşleşmeyen bir manifest yükleme sırasında reddedilir.

{% hint style="danger" %}
Doğrulamadan önce paketin tamamını inceleyin. Bir inceleme belirli baytlar hakkında bir kanıttır; bir güvenlik sertifikası ya da izin vermek değildir.
{% endhint %}

## v0.7'de paket onayı

Doğrulama; işleyici yürütülebilir dosyaları, pakete dahil edilmiş kitaplıklar, bağımlılık kilitleri, başvurular, varlıklar ve yürütme modu bitleri dahil olmak üzere her paket dosyasını ve dizinini kapsar. Bunlardan herhangi birini eklemek, kaldırmak, yeniden adlandırmak ya da değiştirmek onayı geçersiz kılar. Sembolik bağlantılar, sabit bağlantılar, özel dosyalar, ayrıcalıklı modlar, kararsız okumalar ve aşırı büyük dağıtımlar reddedilir. Geçerli yakalama sınırları 4.096 girdi, dosya başına 64 MiB ve paket başına 128 MiB'tır. Bir paketin içinde yok sayılan önbellek ya da durum dizini yoktur.

Daha önce saklanmış, yalnızca meta veriye dayanan onaylar bu sürümde paketleri etkinleştirmez. Dağıtımın tamamını inceleyin, açıkça yeniden doğrulayın, ardından etkinleştirin. Güncellemeler doğrulanıp etkinleştirilene kadar devre dışı kalır; onay asla sessizce taşınmaz. Eski bir makbuz dağıtımın tamamını bağlamıyorsa, yönetilen eklentiler ayrıca yeni ve kimliği doğrulanmış bir mağaza incelemesine ihtiyaç duyar; sıradan doğrulama bu makbuzun yerini tutamaz.

`requires`, kurulu eklenti bağımlılıklarını adlandırır. Doğrulama, onların özyinelemeli onay özetlerini sabitler; her bağımlılık önceden doğrulanmış ve etkinleştirilmiş olmalıdır. Değişen, güncellenen, devre dışı bırakılan ya da iptal edilen bir bağımlılık, önbelleğe alınmış araçlar yoluyla da dahil olmak üzere, ona bağımlı olanları kullanılamaz hale getirir. Kabul aşaması, karma hesaplandıktan sonra yakalanan kapanışın tamamı için etkin ve iptal edilmiş durumu yeniden denetler. Bir bağımlılığı yeniden doğrulamak, onun yeni baytlarını üst paket için sessizce onaylamaz: her üst paketi de inceleyin ve yeniden doğrulayın. Her `approval_digest` kendi paket özetini ve bağımlılık onaylarını bağlar; bu yüzden geçişli bir bağımlılığın değişmesi, üst paketin kendi dosyaları aynı kalsa bile o onayı değiştirir. Döngüsel bağımlılık onayı etkinleşemez. Bağımlılık kapanışı 64 paketle sınırlıdır.

Kayıtlı her çağırma yaşam döngüsü durumunu yeniden denetler ve paketin ve sabitlenmiş bağımlılık kapanışının özel, salt okunur kopyalarını yakalar. Araç sözleşmesi yakalanan manifestle eşleşmelidir. Kayıtlı işleyici bu yakalanmış kopyadan başlatılır; yakalamadan sonra kaynağı düzenlemek bu işleyicinin ya da birlikte gelen dosyalarının yerini alamaz. Yürütme gözlemleri paket, özyinelemeli onay ve bağımlılık özetlerini içerir. Kayıtlı paketler, yerel ve alt düzey bir çağıran daha zayıf bir geçersiz kılma seçmiş olsa bile gerçek çekirdek kısıtlaması gerektirir. Güvenli, bağlantı izlemeyen (no-follow) tanımlayıcı yakalaması ya da çekirdek kısıtlaması olmayan ana sistemler kayıtlı paketleri reddeder. Çağırma zaman aşımları 1–300 saniyeyle sınırlıdır; çağırmanın sahip olduğu süreç grubu, yürütme kopyası kaldırılmadan önce durdurulur.

Kalıcı çıktıları, önbellekleri ve günlükleri yürütülebilir kodun yanında değil, `PLUGIN_SCRATCH` içinde tutun. Salt okunur araçlara yazma izni verilmez. Diğer araçlar geçici alana yazabilir ama yürütme kopyalarını değiştiremez. `VOOL_PLUGIN_DEPENDENCIES`, bildirilen bağımlılık kimliklerinden yakalanan bağımlılık dizinlerine giden bir JSON eşlemesidir. Diğer üçüncü taraf kitaplıkları ve kilit dosyalarını paketin içine koyun; tek başına bir kilit dosyası harici bir ortamı tasdik etmez. Ana sistemin yorumlayıcısı ve sistem kitaplıkları, incelenmiş üçüncü taraf paket baytları olarak gösterilmez; güvenilen platformun parçası olarak kalır. Bu bütünlük denetimi davranışı onaylamaz, onaylı dağıtımın dışından indirilen ya da yüklenen rastgele kodu tasdik etmez, her iç etkiye aracılık etmez ve yerleşik cüzdan onayının yerini almaz.

## Beceri yazma

Beceri, bir talimat dosyası ve bu dosyanın başvurduğu diğer dosyaları içeren bir klasördür:

```text
my-skill/
├── SKILL.md
└── templates/
```

Uygulama içinde bir taslak oluşturun (`skill.create` onu hiçbir şeyi etkinleştirmeden hazırlar), doğrulayın, ardından etkinleştirmek için kurun. Bunlar bilerek ayrı tutulmuş üç adımdır; böylece beceri yazmak asla örtük bir davranış değişikliği olmaz. Katalog ve yazım biçimi, herkese açık sürüm için hâlâ son haline getiriliyor.

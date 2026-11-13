---
description: VOOL'u beceriler ve eklentilerle genişletin.
translation: machine
source_sha256: 4dd2a317a7b10ee024616f6e0c27bd73087c44334f29e2c5f60f69773f4e54e8
---

# Beceriler ve eklentiler

**Beceri**, tekrarlanan bir iş için paketlenmiş bir talimatlar kümesidir. **Eklenti** ise araçlar, bir model sağlayıcısı ya da bir eklenti tarama aracı ekler. Eklentiler beceri de içerebilir.

## Birlikte gelen ve haricen kurulan

VOOL bir **yerleşik beceri kitaplığı** (uygulamanın kendisiyle gelen beceriler) ve güncel derlemelerde **VOOL Database** eklentisiyle birlikte gelir. Kendi beceri ve eklentilerinizi de kurabilirsiniz.

| | Birlikte gelen (VOOL ile gelir) | Haricen kurulan |
| --- | --- | --- |
| Nerede durur | Uygulamanın içinde | Eklenti klasörünüzde (Ayarlar'dan değil, VOOL'daki eklentiler penceresinden yönetilir) |
| Güncellemeler | Uygulamayla birlikte | Paketi güncellediğinizde |
| Devre dışı bırakılabilir mi | Uygulamanın pencerelerinde henüz bir düğme yok | Evet — eklentiler penceresinden |
| İzin verir mi | **Hayır** | **Hayır** |

İki kural her iki kaynak için de geçerlidir:

* **Beceriler öğretir; asla izin vermez.** Bir becerinin bildirdiği araçlar bir *daraltma* sinyalidir (iş akışının araç bütçesi), asla herhangi bir şeye izin değildir. İzin denetleyicisi becerileri okumaz.
* **Tek envanter.** Konsolun gösterdiği katalog ile çalışma zamanının yüklediği paketler aynı listedir. Bir becerinin bir listede görünmesi yüklenebileceğinin kanıtı değildir: her girdi kendi canlı durumunu (kullanılabilir, devre dışı, ön koşul eksik, geçersiz sözleşme) ve nedenini taşır.

## Beceri bulma

Beceriler ve Eklentiler önce VOOL'un kendi becerilerini listeler. **Göz at**, 75 hazır VOOL becerisiyle gelir. Her biri uygulamayla birlikte gelen bir talimatlar kümesidir, bu yüzden Göz at onları çevrimiçi bir arama yapmadan listeler. Bunların 22'si, seçildikleri turlarda VOOL araçlarını (e-postanız, notlarınız, dosyalarınız, PDF'leriniz, cüzdanınız ve daha fazlası) kullanır: bir becerinin araçları yalnızca VOOL'un isteğiniz için zaten sunduklarını daraltır, kapalı bir araç kapalı kalır ve her çağrı her zamanki gibi izin ister. Becerinin **Kaynak ve teknik ayrıntılar** bölümü kullandığı araçları listeler. **Beceri Kodeksi** sekmesi önce VOOL ekibinin becerilerini listeler. Bir beceriyi etkinleştirmeden önce inceleyin.

Göz at'taki bir becerinin nasıl ekleneceği, onu kimin yaptığına bağlıdır:

* **VOOL'un yaptığı beceriler** (yukarıdaki 75 beceri): **Bu beceriyi ekle** düğmesine basın. VOOL, dosyanın kendisiyle birlikte gelen dosyanın tam aynısı olduğunu bilgisayarınızda denetler ve dosyayı riskli talimatlar için okur, ardından sonucu size gösterir. Anahtar gerekmez ve hiçbir yere bir şey gönderilmez. Dosya eşleşmezse VOOL onu reddeder ve bunu söyler.
* **Başka yapımcıların becerileri** (başka yayıncılar ya da yapıştırdığınız bir GitHub bağlantısı): **İndir ve tara** düğmesine basın. VOOL herkese açık beceriyi sabit bir sürümde indirir ve bilgisayarınızda denetler. Bir tarama eklentisini etkinleştirdiyseniz becerinin içeriği ona da gönderilir ve onun raporu VOOL'un kendi raporunun yanına eklenir. Sonucu okuyun, ardından beceriyi ekleyip eklemeyeceğinizi seçin: denetimlerden biri bir sorun bildirirse VOOL beceriyi yalnızca o sürümün risklerini kabul etmenizden sonra ekler. Tarama eklentiniz istemedikçe anahtar gerekmez.

Hiç anahtar olmadan sohbette kendi becerinizi de yapabilirsiniz: bkz. [Kendi becerinizi yapın](../../../guides/make-your-own-skill.md).

Herhangi bir sohbette de sorabilirsiniz: *hangi becerilerim var?* onları listeler, *nasıl beceri eklerim?* ise iki yolu da açıklar.

## Bir beceriyi açma ya da kapatma

**Beceriler ve Eklentiler**'i, ardından **Eklentilerim**'i, ardından **Beceriler**'i açın. Her beceri **Açık** ya da **Kapalı** gösterir ve yanında bir **Etkinleştir** ya da **Devre dışı bırak** düğmesi vardır. Kapattığınız bir beceri, siz yeniden açana kadar hiçbir sohbette kullanılmaz. Dosyaları korunur.

**Eklentiler** altındaki **Built-in skills** kartının kendi düğmesi yoktur: becerilerini **Beceriler** altında tek tek açıp kapatın.

## Bir yanıtı hangi becerinin şekillendirdiğini görme

Bir becerinin talimatları VOOL'un bir yanıtı yazma biçiminin parçası olduğunda, yanıtın altındaki kısa bir satır bunu söyler; örneğin **Kullanılan beceri: Browser**. Bu satır, sohbet açıkken yanıtın hemen ardından görünür; geçmişinizden daha sonra yüklenen yanıtlar bunu henüz göstermez. Çoğu yanıta eşlik eden yanıt biçimi kuralları orada adlandırılmaz.

## VOOL'u geliştirmek için beceriler

Kitaplık, VOOL ekibinin VOOL'u geliştirirken kullandığı becerileri de barındırır: kod incelemesi, test çalıştırmaları, sürüm denetimleri, iş hattı onarımı ve benzerleri. Bunlar sohbette ya da Beceriler sayfasında listelenmez ve gündelik yanıtları yönlendirmez. Geliştiriciler, VOOL'u `VOOL_DEVELOPER_SKILLS=1` ile başlatarak bunları gösterebilir ve kullanabilir.

## Kullanılabilirlik durumları

Bir beceri, türü belli bir nedenle kullanılamaz olarak görünebilir: eksik bir ön koşul (örneğin veritabanı becerisi, veritabanı eklentisinin etkin olmasını gerektirir), bu makinede kullanılamayan bir yetenek, geçersiz bir sözleşme ya da sizin devre dışı bırakmanız. Nedenler her girdi için ayrı gösterilir; devre dışı bırakma girdi başınadır ve yeniden başlatmalardan sonra da korunur.

## Yinelenen kimlikler

İki paket aynı eklenti kimliğini bildirirse (örneğin birlikte gelen bir paket ve kurulmuş bir kopya), birlikte gelen paket **deterministik olarak** tutulur ve bırakılan kopya katalogda bildirilir; asla sessizce gölgelenmez.

## Kendi beceri ve eklentilerinizi kurma

Uygulama içi katalogdan kurun ya da VOOL'u size ait bir eklenti klasörüne yöneltin. Paketler, araçlarını bildiren bir manifest (`plugin.json`) taşır; izin bildirimleri, bildirdiği davranış sınıfıyla eşleşmeyen bir manifest yükleme sırasında reddedilir.

{% hint style="danger" %}
Doğrulamadan önce paketin tamamını inceleyin. Bir inceleme belirli baytlar hakkında bir kanıttır; bir güvenlik sertifikası ya da izin vermek değildir.
{% endhint %}

## Eklentiler sayfasındaki eklentiler

**Eklentiler → Eklentilerim → Eklentiler** yolunu açın. Yerel bir klasörü ya da herkese açık bir GitHub klasörünü tam bir commit'te inceleyin. Kurulum paketin tamamını yakalar ve paketi devre dışı tutar. İmzalı mağaza eklentileri mevcut kayıt zincirini kullanır ve onlar da devre dışı olarak kurulur. Araçları ya da hizmetleri etkinleştirmeden önce erişim sayfasını ayrıca inceleyin. Bir hizmet anahtarını kaydetmek hiçbir istek göndermez; bağlantı testleri ve model listesi yenilemeleri açık eylemlerdir.

Devre dışı bırakma araçları kaldırır ve çalışan çağrıları durdurur. Kaldırma önce devre dışı bırakır, eklentiye ait dosyaları ve geçici alanı kaldırır, varsayılan olarak kayıtlı anahtarları siler ve geçmişi korur. Güncellemeler ve yeniden kurulumlar yeni bir inceleme gerektirir ve devre dışı başlar. Başka bir depodan ya da yayıncıdan gelen bir güncelleme kayıtlı anahtarları korumaz. UsePod ve Eyebrow artık VOOL'un parçası değildir ve VOOL ikisi için de eklenti sunmaz. Kayıtlı bir UsePod ya da Eyebrow anahtarı bu bilgisayarda kalır ve VOOL onu kullanmaz; siz bildirimi kapatana kadar Eklentiler bunu söyler. UsePod modeli kullanan sohbetlerde yükseltmeden sonra seçili model olmaz ve Otomatik bu sohbetlerde bir bulut modeline geçmez; siz kapatana kadar bir bildirim bunu söyler.

Bkz. [Eklenti yazma](../../../guides/plugin-authoring.md) ve [Eklenti denetimleri](addon-security.md).

## Tam paket onayı

Doğrulama; işleyici yürütülebilir dosyaları, pakete dahil edilmiş kitaplıklar, bağımlılık kilitleri, başvurular, varlıklar ve yürütme modu bitleri dahil olmak üzere her paket dosyasını ve dizinini kapsar. Bunlardan herhangi birini eklemek, kaldırmak, yeniden adlandırmak ya da değiştirmek onayı geçersiz kılar. Sembolik bağlantılar, sabit bağlantılar, özel dosyalar, ayrıcalıklı modlar, kararsız okumalar ve aşırı büyük dağıtımlar reddedilir. Geçerli yakalama sınırları 4.096 girdi, dosya başına 64 MiB ve paket başına 128 MiB'tır. Bir paketin içinde yok sayılan önbellek ya da durum dizini yoktur.

Daha önce saklanmış, yalnızca meta veriye dayanan onaylar bu sürümde paketleri etkinleştirmez. Dağıtımın tamamını inceleyin, açıkça yeniden doğrulayın, ardından etkinleştirin. Güncellemeler doğrulanıp etkinleştirilene kadar devre dışı kalır; onay asla sessizce taşınmaz. Eski bir makbuz dağıtımın tamamını bağlamıyorsa, yönetilen eklentiler ayrıca yeni ve kimliği doğrulanmış bir mağaza incelemesine ihtiyaç duyar; sıradan doğrulama bu makbuzun yerini tutamaz.

`requires`, kurulu eklenti bağımlılıklarını adlandırır. Doğrulama, onların özyinelemeli onay özetlerini sabitler; her bağımlılık önceden doğrulanmış ve etkinleştirilmiş olmalıdır. Değişen, güncellenen, devre dışı bırakılan ya da iptal edilen bir bağımlılık, önbelleğe alınmış araçlar yoluyla da dahil olmak üzere, ona bağımlı olanları kullanılamaz hale getirir. Kabul aşaması, karma hesaplandıktan sonra yakalanan kapanışın tamamı için etkin ve iptal edilmiş durumu yeniden denetler. Bir bağımlılığı yeniden doğrulamak, onun yeni baytlarını üst paket için sessizce onaylamaz: her üst paketi de inceleyin ve yeniden doğrulayın. Her `approval_digest` kendi paket özetini ve bağımlılık onaylarını bağlar; bu yüzden geçişli bir bağımlılığın değişmesi, üst paketin kendi dosyaları aynı kalsa bile o onayı değiştirir. Döngüsel bağımlılık onayı etkinleşemez. Bağımlılık kapanışı 64 paketle sınırlıdır.

Kayıtlı her çağırma yaşam döngüsü durumunu yeniden denetler ve paketin ve sabitlenmiş bağımlılık kapanışının özel, salt okunur kopyalarını yakalar. Araç sözleşmesi yakalanan manifestle eşleşmelidir. Kayıtlı işleyici bu yakalanmış kopyadan başlatılır; yakalamadan sonra kaynağı düzenlemek bu işleyicinin ya da birlikte gelen dosyalarının yerini alamaz. Yürütme gözlemleri paket, özyinelemeli onay ve bağımlılık özetlerini içerir. Kayıtlı paketler, yerel ve alt düzey bir çağıran daha zayıf bir geçersiz kılma seçmiş olsa bile gerçek çekirdek kısıtlaması gerektirir. Güvenli, bağlantı izlemeyen (no-follow) tanımlayıcı yakalaması ya da çekirdek kısıtlaması olmayan ana sistemler kayıtlı paketleri reddeder. Çağırma zaman aşımları 1–300 saniyeyle sınırlıdır; çağırmanın sahip olduğu süreç grubu, yürütme kopyası kaldırılmadan önce durdurulur.

Kalıcı çıktıları, önbellekleri ve günlükleri yürütülebilir kodun yanında değil, `PLUGIN_SCRATCH` içinde tutun. Salt okunur araçlara yazma izni verilmez. Diğer araçlar geçici alana yazabilir ama yürütme kopyalarını değiştiremez. `VOOL_PLUGIN_DEPENDENCIES`, bildirilen bağımlılık kimliklerinden yakalanan bağımlılık dizinlerine giden bir JSON eşlemesidir. Diğer üçüncü taraf kitaplıkları ve kilit dosyalarını paketin içine koyun; tek başına bir kilit dosyası harici bir ortamı tasdik etmez. Çalışma zamanı yorumlayıcısı ve sistem kitaplıkları, incelenmiş üçüncü taraf paket baytları olarak gösterilmez; güvenilen platformun parçası olarak kalır. Bu bütünlük denetimi davranışı onaylamaz, onaylı dağıtımın dışından indirilen ya da yüklenen rastgele kodu tasdik etmez, her iç etkiye aracılık etmez ve yerleşik cüzdan onayının yerini almaz.

macOS'ta eklenti kodu macOS sanal alanında (sandbox) çalışır. Anahtarlar bir işleyiciye asla ortamı üzerinden değil, yalnızca kendi özel girdisi üzerinden ulaşır; çünkü bir Mac'te aynı kullanıcının herhangi bir programı, eklenti kodu da dahil, başka bir programın başlangıç ortamını okuyabilir. Ortamında bir anahtara ihtiyaç duyan bir bağlantı sunucusu yalnızca isteğe bağlı yalıtılmış Linux çalışma ortamında çalışır; bu ortamı kendiniz hazırlarsınız. VOOL asla bir sanal makine başlatmaz ve bir imaj indirmez. Bu ortam olmadan o eklenti kapalı kalır ve nedenini söyler. Linux'ta, özel bir süreç ad alanı ve `/proc` dahil olmak üzere bwrap kullanılır.

macOS'ta ağ kullandığını bildiren bir araç yalnızca VOOL'un her çağrı için başlattığı özel bir proxy'ye ulaşabilir ve proxy yalnızca bildirilen ana makinelere bağlanır. Linux'ta ve yalıtılmış çalışma ortamında eklenti süreçlerinin ağ erişimi yoktur. Yerleşik sağlayıcı ve tarama istekleri her platformda kendi ayrı, bildirilen ana makinelere yönelik HTTPS taşımalarını kullanır.

## Beceri yazma

* Sohbette isteyerek bir beceri yapmak için bkz. [Kendi becerinizi yapın](../../../guides/make-your-own-skill.md).
* Bir SKILL.md dosyasını kendiniz yazmak, lint ile denetlemek, kurmak ve test etmek için bkz. [Bir beceri oluşturun ve test edin](../../../guides/build-and-test-a-skill.md).

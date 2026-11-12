---
description: Bir hatayı güvenle bildirme: hassas bilgileri karartılmış taslaklar, tam bayt onayı ve makinenizden asla çıkmayanlar.
translation: machine
source_sha256: 4e64e6313c1f6a8be6784a1f35cedc87b81a5661fdb548c6821109f935db818f
---

# Hata bildirimi ve gizlilik

## Yerleşik hata bildiricisi

Uygulama içi hata bildiricisini (ya da `python -m apps.vool_cli bug-report` CLI'sini) kullanın. Kendi kendine asla hiçbir şey göndermez; akış bilerek üç aşamalıdır:

1. **Taslak** — son günlüklerden ve tanılama bilgilerinden yerel olarak bir rapor oluşturulur ve oluşturulurken hassas bilgiler karartılır. *"Nothing is sent yet — a local draft is created first."*
2. **Önizleme** — gönderilecek **tam baytları**, yükün karma değeri ve neyin kaldırıldığını listeleyen bir karartma özetiyle birlikte görürsünüz. Göndermek istemediğiniz alanlar (günlükler, bayraklar, yeniden oluşturma notları, ekler) kaldırılabilir; her düzenleme onayı sıfırlar.
3. **Onay** — göndermek için tam olarak bu baytları açıkça onaylamanız gerekir. Yük değişirse onay artık geçerli olmaz ve yeniden onaylamanız gerekir.

Onayladıktan sonra **Open on GitHub**, tarayıcınızda herkese açık [vool-reports](https://github.com/Parad0x-Labs/vool-reports) deposunda doldurulmuş bir rapor sayfası açar. Orada bir kez daha okuyun ve Submit'e basın. VOOL'un kendisi hiçbir şey göndermez ve GitHub belirteci gerekmez. **Copy report** ve **Save a local copy** hiç onay gerektirmez: size aynı temizlenmiş baytları verir ve makinenizden hiçbir şey çıkmaz. **My reports**, taslaklarınızı ve her birinin nasıl çıktığını listeler.

## Yanlış bir yanıtı bildirme

Herhangi bir yanıtın altındaki **Bildir**'e ya da 👎'ye basın veya yanlış kısmı vurgulayıp **Bildir**'e basın. Rapor yalnızca o kısmı (ya da yanıtın tamamını) alıntılar. Kutuyu işaretlemediğiniz sürece sorunuz dahil edilmez. Yanıtın makbuzu eklenir: model, sağlayıcı, token'lar, zaman ve hangi adımların çalıştığı; adımların içeriği asla. VOOL'dan unutmasını istediğiniz bir yanıt bildirilemez.

## İncelemeye yardımcı olmak için neler eklenir

Bilgisayarınızın yapısı: işletim sistemi sürümü, çip, bellek, GPU ve yerel model sunucusunun sürümü. Ayrıca kısa bir sağlık denetimi: yerel model sunucusunun yanıt verip vermediği ve bir aralık olarak boş disk alanı. Bilgisayarınızın adı, kullanıcı adınız, seri numaraları ya da ağ adresleri asla eklenmez. Bunlardan herhangi birini önizlemede kaldırabilirsiniz.

## Karartma neleri kaldırır

Taslağı görmenizden önce, deterministik olarak: kimlik bilgileri ve kimlik doğrulama başlıkları, ev dizini yolları ve kullanıcı adları, bilgisayarınızın adı, e-postalar, IP adresleri, PIN'ler ve tek kullanımlık kodlar, kart ve banka (IBAN) numaraları, telefon numaraları, cüzdan adresleri, profilinizde kayıtlı kişisel bilgiler, etiketlenmiş gizli bilgiler ve yüksek entropili veri blokları; bunların üstünde de son bir gizli bilgi taraması katmanı bulunur. VOOL sıradan cümlelerde yazılmış adları ya da özel bilgileri tanıyamaz, bu yüzden önizlemeyi okuyun. Yığın izleri ham olarak yapıştırılmaz, bir izin listesinden yeniden oluşturulur. Raporlardaki hata kanıtları **yalnızca türü belli kodlar** taşır; asla iç bağlam ya da neden zincirleri taşımaz.

Kaldırılan her değer bir işaretçiyle değiştirilir. Bir e-posta, bir kişi, bir ev klasörü, bir IP adresi, bir telefon numarası, bir cüzdan adresi, bilgisayarınızın adı ya da profilinizden bir bilgi, rapor boyunca tek bir numaralı işaretçiyi korur; örneğin iki farklı adres için `[EMAIL_1]` ve `[EMAIL_2]`. Böylece rapor yine anlaşılır okunur. Kaldırılan her anahtar, parola, PIN, kart ya da banka numarası numarasız olarak `[SECRET_REMOVED]` şeklinde görünür. Bir işaretçiyi gerçek değere bağlayan liste, siz rapor üzerinde çalışırken yalnızca VOOL'un belleğinde tutulur; böylece bir düzenleme aynı işaretçileri korur. Liste asla kaydedilmez ya da gönderilmez ve raporu sildiğinizde, bir saat kullanılmadığında ya da VOOL yeniden başladığında unutulur.

## VOOL'un asla yapmadıkları

* Hiçbir türde otomatik harici raporlama yapmaz.
* Bir ayarın arkasında bekleyen bir telemetri hattı yoktur.
* Güvenlik sorunları, deponun [güvenlik politikasındaki](https://github.com/Parad0x-Labs/vool/security/policy) gizli kanaldan iletilir; asla herkese açık bir issue olarak değil.

## Gizlilik özeti

* Yerel bir model seçiliyken istemler ve dosyalar makinenizde işlenir.
* Bulut çağrıları, sağladığınız ve yerel olarak saklanan API anahtarını kullanır; anahtarlar asla raporlarla gitmez.
* Veri akışının tüm ayrıntıları [Verilerin işlenmesi](../trust/data-handling.md) sayfasındadır.

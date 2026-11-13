---
description: VOOL, yapay zekâ modellerini bilgisayarınızda yerel olarak çalıştıran ya da kendi anahtarınızla bulut modellerine bağlanan bir masaüstü yapay zekâ asistanıdır. Ücretsiz ve gizli. Mac (Apple Silicon ve Intel) ve Linux için indirmeler; Windows deneyseldir ve kaynak koddan kurulur.
translation: machine
source_sha256: dc6715130dd5dcfe7a6adc926aeff095413e59b7eecc1735d1c2ee1d88990ac5
---

# VOOL

VOOL bir masaüstü yapay zekâ asistanıdır. Bilgisayarınıza bir uygulama olarak kurulur ve yapay zekâ
modellerini o makinede yerel olarak çalıştırabilir ya da kendi API anahtarınızla bir bulut yapay zekâ
sağlayıcısına bağlanabilir. Yaptığı her işlemin okunabilir bir kaydını tutar.

Ücretsizdir. Yerel bir modelle sohbetleriniz ve dosyalarınız internet bağlantısına ihtiyaç duymaz; hava durumu veya fiyatlar
gibi güncel bilgiler yalnızca "Web aramaları" açıkken herkese açık web hizmetlerinden gelir. Mac (Apple Silicon ve Intel) ve Linux x86_64 için
indirmeler vardır; Windows deneyseldir ve kaynak koddan kurulur.

Bu belgeler VOOL'u kurmayı, bir model bağlamayı ve VOOL'un dosyalarınızı, hafızanızı ve anahtarlarınızı
nasıl ele aldığının ardındaki kavramları anlatır.

## Nasıl bir sonuç alıyor

Daha önce hiç kullanılmamış 150 LoCoMo hafıza sorusunda, her ajan için aynı model yanıt verdiğinde ve
aynı GPT-4o değerlendirdiğinde, VOOL'un hafızası diğer iki açık kaynak ajanı geride bıraktı:

| | VOOL | OpenClaw | Hermes |
| --- | --- | --- | --- |
| Doğru yanıtlar | **%82,7** (124 / 150) | %71,3 (107) | %62,7 (94) |
| Soru başına token | **9,6k** | 51,8k | 33,5k |
| Soru başına maliyet | **0,0033 USD** | 0,0100 USD | 0,0046 USD |

Bu, OpenClaw'dan 5,4 kat, Hermes'ten 3,5 kat daha az token demektir ve yanıt başına maliyet OpenClaw'un
üçte biridir. VOOL 0.7 ile gelen hafıza aynı sorularda 150 sorudan 123'ünü bildi.
Her çalıştırma, kurulum ve zayıf noktalar yayımlanmıştır: [hafıza karşılaştırmaları](https://github.com/Parad0x-Labs/vool/tree/main/docs/benchmarks).

## Buradan başlayın

| Ne yapmak istiyorsunuz | Okuyun |
| --- | --- |
| VOOL'u çalıştırmak | [Kurulum](getting-started/install.md) |
| İlk ekranı anlamak | [İlk çalıştırma](getting-started/first-run.md) |
| Ona bir model göstermek | [Model bağlama](getting-started/connect-a-model.md) |
| Hangi harcamaların olabileceğini denetlemek | [Harcama sınırları](guides/spending-limits.md) |
| Makinenizden neyin çıktığını bilmek | [Yerel ve bulut](concepts/local-and-cloud.md) |

## VOOL nedir

* **Bir masaüstü uygulaması.** Bilgisayarınıza kurulur; bir tarayıcı sekmesi ya da barındırılan bir
  hizmet değildir.
* **Sohbetler ve dosyalar için çevrimdışı çalışır.** Yerel bir model seçiliyken sohbetleriniz ve dosyalarınız internet
  bağlantısına ihtiyaç duymaz. Hava durumu veya fiyatlar gibi güncel bilgiler yalnızca "Web aramaları" açıkken herkese açık web hizmetlerinden gelir (yeni bir kurulumda kapalıdır), "Yalnızca yerel" modunda ise asla gelmez.
* **Varsayılan olarak yerel.** Yerel bir model seçiliyken istemler ve dosyalar
  makinenizde işlenir.
* **Modelden bağımsız.** Hem yerel çalışma ortamları hem de bulut sağlayıcıları seçilebilir. Bulut anahtarını
  siz sağlarsınız; anahtar makinenizde kalır.
* **İncelenebilir.** Her araç çağrısı girdileri ve sonuçlarıyla birlikte kaydedilir; böylece bir çalıştırma
  sonradan yeniden okunabilir.

{% hint style="warning" %}
VOOL; Apple Silicon Mac'ler, Intel Mac'ler ve Linux x86_64 için beta aşamasındadır. Mac uygulamaları
henüz Apple tarafından onaylanmadığı (noter tasdikli olmadığı) için macOS, birini ilk kez açtığınızda onay ister;
[Kurulum](getting-started/install.md#macte-ilk-açılış) sayfası nasıl yapılacağını gösterir. Kurmadan önce
[Sürüm durumu](trust/release-status.md) sayfasına bakın.
{% endhint %}

## Açıkça iki yanıt yolu

VOOL iki yolla yanıt verir ve sohbet başlığı bir sonraki isteğin hangi yolla çalışacağını her zaman gösterir; örneğin
**Otomatik** ile **Önce yerel**:

1. **Yerel** — iş bu bilgisayarda kalır.
2. **Bulut** — seçilen bağlam, makinenizden ve sizin anahtarınızla, seçtiğiniz sağlayıcıya
   doğrudan gönderilir.

Arada Parad0x tarafından işletilen bir aktarıcı da yoktur. Ayrıntılar
[Verilerin işlenmesi](trust/data-handling.md) sayfasındadır.

## Yardım almak

* [SSS](help/faq.md) — en sık sorulan sorular
* [Sorun giderme](help/troubleshooting.md) — bir şey başlamadığında
* [Discord](https://discord.gg/V9NkjP3Fzz) — ekibe sorun

---
description: VOOL ile yapay zekâ modellerini kendi bilgisayarınızda yerel olarak çalıştırma: yerel bir model sunucusu kurma, model boyutu seçme ve tamamen çevrimdışı çalışma.
translation: machine
source_sha256: a19d4790729d13ed5e1b134bcb67ed3e7a80609561be4227ab9480492100c8a6
---

# Yerel model çalıştırma

## Bir çalışma ortamı kurun

VOOL, yerel modelleri çalıştırmak için varsayılan olarak Ollama'yı kullanır; OpenAI uyumlu bir uç noktası olan diğer yerel çalışma ortamları da çalışır. Ollama ayrı bir programdır ve [ollama.com](https://ollama.com) adresinden ayrıca kurulur. macOS uygulaması kendi yerel model çalıştırıcısını içerir, bu yüzden Mac'te bu adımı atlayabilirsiniz. Linux'ta ve kaynak koddan kurulumlarda Ollama'yı kurun, başlatın ve bir model indirin (pull).

```bash
ollama serve
ollama pull qwen3:4b
```

VOOL varsayılan olarak yerel modeli sizin için seçer: bilgisayarınıza göre boyutlandırılmış bir Qwen modeli. Birçok bilgisayarda bu model `qwen3:4b` olur; 32 GB veya daha fazla belleği olan bilgisayarlarda varsayılan model daha büyük bir Qwen modelidir.

## VOOL'u ona yöneltin

**Ayarlar → Modeller ve sağlayıcılar**'ı açın. Çalışan yerel çalışma ortamları otomatik olarak algılanır. Bir model seçin; mod göstergesi Yerel olur.

## Bir kez hazırlayın

Yerel bir model ancak VOOL onu denetledikten sonra yanıt yazar. **Ayarlar → Modeller ve sağlayıcılar → Yerel modeller**'i açın ve modelin yanındaki **Bu modeli kullan**'a basın. VOOL, modelin VOOL'un araçlarını sizin yerinize yanıt verebilecek kadar iyi kullanabildiğini bilgisayarınızda sessizce denetler. Denetim bir iki dakika sürebilir; hiçbir şey indirilmez ve kimseyle iletişim kurulmaz. Denetim geçtiğinde model **Hazır** olarak görünür ve VOOL onunla yanıt verir. O zamana kadar VOOL onun son yanıtları yazmasına izin vermez.

* **Uygun değil:** model VOOL'un araçlarını yeterince iyi kullanamıyor. VOOL bunun yerine neler yapabileceğinizi listeler: bu bilgisayar için seçtiği modeli kullanmak ya da indirmek veya bir bulut modeli kullanmak.
* **Kontrol bitmedi:** Ollama'nın açık olduğundan emin olun, ardından **Tekrar dene**'ye basın. Yavaş bir bilgisayarın ikinci bir denemeye ihtiyacı olabilir.

VOOL başladığında hazır bir yerel model yoksa, önce sizin seçtiğiniz model olmak üzere bu bilgisayardaki modellerden en fazla üçünü denetler ve denetimi geçen ilk modeli kullanır. Bu bilgisayar için fazla büyük bir modeli atlar. Hazır bir model her zaman hazır olmayan bir modelden önce gelir.

## Boyutlandırma

| Model boyutu | Gereken bellek | Uygun olduğu işler |
| --- | ---: | --- |
| 3B | ~4 GB | Kısa düzenlemeler, hızlı sorular |
| 7–8B | ~8 GB | Genel kullanım, kodlama işlerinin çoğu |
| 14B | ~16 GB | Daha uzun akıl yürütme |
| 32B+ | 32 GB veya daha fazla | Daha ağır işler, daha yavaş |

{% hint style="info" %}
Kuantize derlemeler bellek ihtiyacını önemli ölçüde azaltır. 4 bitlik 7B bir model yaklaşık 5 GB'ta rahatça çalışır.
{% endhint %}

## Yavaşsa

* Daha küçük ya da daha yoğun kuantize edilmiş bir model seçin.
* Belleği çok kullanan diğer uygulamaları kapatın.
* Bağlamı kısaltın: istekte daha az dosya, işlenecek daha az şey demektir.

---
description: VOOL ile yapay zekâ modellerini kendi bilgisayarınızda yerel olarak çalıştırma: yerel bir model sunucusu kurma, model boyutu seçme ve tamamen çevrimdışı çalışma.
translation: machine
source_sha256: d93b9995b0f4fec8aca12bb90d8d6f9ff0fd9623c43d4529913dc76f055ac24c
---

# Yerel model çalıştırma

## Bir çalışma ortamı kurun

VOOL yerel bir model sunucusuyla konuşur. macOS uygulaması kendi yerel model çalıştırıcısını içerir, bu yüzden Mac'te bu adımı atlayabilirsiniz. Linux'ta ve kaynak koddan kurulumlarda Ollama gibi bir sunucu kurun, başlatın ve bir model indirin (pull).

```bash
ollama serve
ollama pull qwen2.5-coder:7b
```

## VOOL'u ona yöneltin

**Ayarlar → Modeller ve sağlayıcılar**'ı açın. Çalışan yerel çalışma ortamları otomatik olarak algılanır. Bir model seçin; mod göstergesi Yerel olur.

## Bir kez sertifikalayın

Yerel bir model ancak sertifikalandıktan sonra yanıt yazar. **Ayarlar → Modeller ve sağlayıcılar → Yerel modeller**'i açın, modeli kaydedin, ardından **Certify**'ı seçin. Sertifikalama makinenizde kısa bir araç denetimi çalıştırır; hiçbir şey indirilmez ve kimseyle iletişim kurulmaz. Bir model denetimi geçene kadar VOOL onun son yanıtları yazmasına izin vermez.

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

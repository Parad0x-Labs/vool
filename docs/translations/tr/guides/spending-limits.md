---
description: Harcama sınırları, fiyat korumaları ve ücretli bir çağrı yarıda kesildiğinde ne olduğu.
translation: machine
source_sha256: a4a5246059942702330cbfc18e9872dc5b0f46192fa5b852bfc9060ca987eae6
---

# Harcama sınırları ve fiyat korumaları

VOOL, sizin denetlediğiniz açık bir üst sınır olmadan asla sizin adınıza para harcamaz. Bu sayfa, ücretli model çağrılarını ve ücretli kaynakları koruyan katmanları ve ücretli bir çağrı yarıda kesildiğinde ne olduğunu anlatır.

## İki fiyat, tek kural

* **Katalog fiyatı** — bir sağlayıcının bir model için o anda ilan ettiği fiyat. VOOL onu bulur; bu bir bilgidir, izin değildir.
* **Kabul ettiğiniz fiyat** — Ayarlar'da o sağlayıcı ve model için onayladığınız üst sınır. Üst sınırınızın üzerinde bulunan bir fiyat, sağlayıcıyla iletişim kurulmadan **önce** reddedilir. Ret tam olarak bunu söyler: model çalışmadı, hiçbir şey gönderilmedi, hiçbir ücret alınmadı.

## Harcama tavanları

Ücretli çağrılarda VOOL her çağrıdan önce tavanlara karşı para ayırır:

| Tavan | Anlamı |
| --- | --- |
| Çağrı başına | Tek bir çağrının öngörülen maliyet üst sınırı. |
| Görev başına | Bir görevin tüm çağrılarında harcayabileceği en yüksek tutar. |
| Günlük / aylık | Ücretli kullanım için dönemin tamamına ait üst sınırlar. |

Bir tavanı aşacak bir çağrı gönderilmeden önce reddedilir. Ret, onu reddeden tavanı adlandırır (`per_task_spend_cap_exceeded` ve benzerleri) ve ifade bilerek gönderim öncesini anlatır: *"running it would exceed your spend cap"* — asla sağlayıcıyla iletişim kurulduğu iddiası değil.

## Sabitlenmiş bir model asla sessizce değiştirilmez

Ücretli bir modeli açıkça seçerseniz ve bir koruma onu reddederse VOOL sessizce daha ucuz bir modelle yanıt **vermez**. Tur; seçtiğiniz modelin adı, gerçekte engelleyen kapının adı ve *"no other model was substituted."* cümlesiyle başarısız olur. Kendiniz farklı bir model seçin ya da tavanı bilinçli olarak yükseltin.

## Ücret alındı ama yanıt hiç gelmedi

Ücretli bir çağrı için ücret alındıysa ama yanıtı gelmediyse sonuç **bilinmiyor** olarak kalır; VOOL başarısız ya da başarılı olmuş gibi davranmaz:

* Yerine hiçbir yanıt uydurulmaz ve para iadesi varsayılmaz.
* Ödeme, çözümlenmemiş olarak kayıtta tutulur.
* Ayarlar onu **Paid calls waiting for an answer** altında listeler; orada **Resume** aynı yanıtı **yeniden ödeme yapmadan** bir kez ister.

Bu yüzden bu durumlarda körü körüne yeniden denemek asla önerilmez: düz bir yeniden gönderim iki kez ödeme yapabilir. Desteklenen tek yol Resume'dur.

## Otomatik cüzdan ödemeleri (x402)

Base ya da Base Sepolia üzerinde USDC ile x402 ödemesi isteyen web siteleri, bu tavanlar içinde tek bir PIN onayıyla Crypto Pilot cüzdanınızdan ödenebilir; bununla henüz canlı bir mainnet ödemesi yapılmadı. Ayarlar'daki model pazar yerindeki çağrı başına ödeme seçeneği, bu tavanlar içinde her çağrı için bir onayla Solana Mainnet cüzdanından öder ve henüz gerçek bir zincirde test edilmedi. Cüzdanla ödenen kaynakların (bkz. [Cüzdan](../guides/wallet.md)) kendi otomatik ödeme tavanı vardır. Tavandan fazlasını isteyen bir kaynak basitçe ödenmez; tavanı bilinçli olarak yükseltebilir ya da kaynağı onayınızla açık bir öneri üzerinden ödeyebilirsiniz.

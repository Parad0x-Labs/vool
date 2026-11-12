---
translation: machine
source_sha256: 72ade2152db88157a95e2155800ad7f7ecbca477e44e40d1aac1d95495bbc42e
---

# VOOL Hata Kitabı

<!-- GENERATED FILE — do not hand-edit. Regenerate with `python -m tools.generate_error_book`. -->

<!-- GENERATED FILE: the app's own texts for each code, and the words in tools/docs/error_book_words.json. Regenerate with tools/docs/translate_error_book.py, never by hand. -->

VOOL'un size gösterebileceği her hata, uygulamanın kendi hata kataloğundan üretilmiştir: aşağıdaki kodlar, mesajlar ve sonraki adımlar tam olarak uygulamanın gösterdikleridir.

- **Şema:** `vool.fault.v1`
- **Katalog sürümü:** `1`
- **Katalog özeti:** `83290d22af3092c2aee6c9a3a5fe466107140dd7d52ab2aab1e8d817dc548b64`
- **Hata sayısı:** 58

Her kod sabittir ve asla farklı bir anlam için yeniden kullanılmaz. Her kodun aşağıda, başlığı kodun kendisi olan ayrı bir bölümü vardır; bu yüzden bu sayfada kodu size verildiği gibi aradığınızda bölümünü bulursunuz, bölüme bağlantı da bu sayfanın adresinin ardından `#` ve alt çizgileri kısa çizgiye çevrilmiş koddan oluşur. Her bölüm ne olduğunu, neyin zaten olmuş olabileceğini, sonra ne yapılacağını ve tekrar denemenin işe yarayıp yaramayacağını söyler. Size verilen bir kod bu kitapta yoksa VOOL'un bu sürümünden değildir: onu hangi sürümün gösterdiğini kontrol edin ya da kodu yazıldığı gibi belirterek bildirin.

## Güvenlik

| Kod | Başlık | Önem derecesi | Zaten ne olmuş olabilir |
|---|---|---|---|
| [`confinement_refusal`](#confinement-refusal) | İzin verilen kapsam dışında değişiklik | yüksek | Hiçbir şey gönderilmedi, imzalanmadı veya değiştirilmedi |
| [`credential_failure`](#credential-failure) | Saklı kimlik bilgisi okunamadı | yüksek | Hiçbir şey gönderilmedi, imzalanmadı veya değiştirilmedi |
| [`wallet_backup_unavailable`](#wallet-backup-unavailable) | Yedek zaten bir kez gösterildi | yüksek | Hiçbir şey gönderilmedi, imzalanmadı veya değiştirilmedi |
| [`wallet_caller_refused`](#wallet-caller-refused) | VOOL'un kendi penceresinden başlatılmadı | yüksek | Hiçbir şey gönderilmedi, imzalanmadı veya değiştirilmedi |
| [`wallet_card_data_refused`](#wallet-card-data-refused) | Kart verisi reddedildi | yüksek | Hiçbir şey gönderilmedi, imzalanmadı veya değiştirilmedi |
| [`wallet_chain_identity_mismatch`](#wallet-chain-identity-mismatch) | Zincir ucunun kimliği kanıtlanmadı | yüksek | Bir istek harici bir hizmete ulaştı; hiçbir ödeme yapılmadı |
| [`wallet_environment_inactive`](#wallet-environment-inactive) | Yanlış ağ ortamı | orta | Hiçbir şey gönderilmedi, imzalanmadı veya değiştirilmedi |
| [`wallet_export_pin_not_accepted`](#wallet-export-pin-not-accepted) | Dışa aktarma yalnızca cihaz kimlik doğrulamasıyla açılır | yüksek | Hiçbir şey gönderilmedi, imzalanmadı veya değiştirilmedi |
| [`wallet_export_refused`](#wallet-export-refused) | Eski dışa aktarma yolu kullanımdan kaldırıldı | yüksek | Hiçbir şey gönderilmedi, imzalanmadı veya değiştirilmedi |
| [`wallet_export_unavailable`](#wallet-export-unavailable) | Bu cüzdan için dışa aktarma kullanılamıyor | yüksek | Hiçbir şey gönderilmedi, imzalanmadı veya değiştirilmedi |
| [`wallet_legacy_surface_retired`](#wallet-legacy-surface-retired) | Eski para yolu kullanımdan kaldırıldı | yüksek | Hiçbir şey gönderilmedi, imzalanmadı veya değiştirilmedi |
| [`wallet_network_disabled`](#wallet-network-disabled) | Ağ bu cüzdan için bildirilmemiş | yüksek | Hiçbir şey gönderilmedi, imzalanmadı veya değiştirilmedi |
| [`wallet_outbound_refused`](#wallet-outbound-refused) | İstek onaylı uçların dışında | yüksek | Hiçbir şey gönderilmedi, imzalanmadı veya değiştirilmedi |
| [`wallet_quote_mismatch`](#wallet-quote-mismatch) | Onay artık önizlemeyle uyuşmuyor | yüksek | Hiçbir şey gönderilmedi, imzalanmadı veya değiştirilmedi |
| [`wallet_recovery_refused`](#wallet-recovery-refused) | Kurtarma kanıtı bu cüzdana uymuyor | yüksek | Hiçbir şey gönderilmedi, imzalanmadı veya değiştirilmedi |
| [`wallet_signature_invalid`](#wallet-signature-invalid) | İmza cüzdanın anahtarıyla uyuşmadı | yüksek | Bir istek harici bir hizmete ulaştı; hiçbir ödeme yapılmadı |
| [`wallet_unlock_throttled`](#wallet-unlock-throttled) | Başarısız kilit açma denemelerinden sonra cüzdan kilitlendi | yüksek | Hiçbir şey gönderilmedi, imzalanmadı veya değiştirilmedi |

## Bütünlük

| Kod | Başlık | Önem derecesi | Zaten ne olmuş olabilir |
|---|---|---|---|
| [`evidence_corruption`](#evidence-corruption) | Yanıt kayıtları tutarlılık denetiminden geçemedi | yüksek | Yerel bir kayıt veya değişiklik yapıldı |
| [`integrity_verification_failure`](#integrity-verification-failure) | Kayıt bütünlük denetiminden geçemedi | kritik | Yerel bir kayıt veya değişiklik yapıldı |
| [`unsupported_claim`](#unsupported-claim) | Desteklenmeyen ifadeler tutuldu | orta | Yerel bir kayıt veya değişiklik yapıldı |
| [`wallet_duplicate_payment`](#wallet-duplicate-payment) | Ödeme zaten önerildi veya gönderildi | orta | Yerel bir kayıt veya değişiklik yapıldı |

## İlke

| Kod | Başlık | Önem derecesi | Zaten ne olmuş olabilir |
|---|---|---|---|
| [`evm_pocket_custody_unavailable`](#evm-pocket-custody-unavailable) | Eski cep yalnızca Solana için | düşük | Hiçbir şey gönderilmedi, imzalanmadı veya değiştirilmedi |
| [`permission_denied`](#permission-denied) | İzin reddedildi | orta | Hiçbir şey gönderilmedi, imzalanmadı veya değiştirilmedi |
| [`wallet_acknowledgement_required`](#wallet-acknowledgement-required) | Onay gerekli | düşük | Hiçbir şey gönderilmedi, imzalanmadı veya değiştirilmedi |
| [`wallet_amount_invalid`](#wallet-amount-invalid) | Miktar bu coin için kesin değil | düşük | Hiçbir şey gönderilmedi, imzalanmadı veya değiştirilmedi |
| [`wallet_approval_rejected`](#wallet-approval-rejected) | Onay kabul edilmedi | orta | Hiçbir şey gönderilmedi, imzalanmadı veya değiştirilmedi |
| [`wallet_confirmation_required`](#wallet-confirmation-required) | Yazılı onay gerekli | düşük | Hiçbir şey gönderilmedi, imzalanmadı veya değiştirilmedi |
| [`wallet_credential_mismatch`](#wallet-credential-mismatch) | Girişler birbirini tutmadı | düşük | Hiçbir şey gönderilmedi, imzalanmadı veya değiştirilmedi |
| [`wallet_dependency_unavailable`](#wallet-dependency-unavailable) | EVM kitaplıkları kurulu değil | orta | Hiçbir şey gönderilmedi, imzalanmadı veya değiştirilmedi |
| [`wallet_device_auth_denied`](#wallet-device-auth-denied) | Cihazla kilit açma reddedildi | düşük | Hiçbir şey gönderilmedi, imzalanmadı veya değiştirilmedi |
| [`wallet_disabled`](#wallet-disabled) | Cüzdan kapalı | düşük | Hiçbir şey gönderilmedi, imzalanmadı veya değiştirilmedi |
| [`wallet_insufficient_funds`](#wallet-insufficient-funds) | Bakiye miktarı ve ücreti karşılamıyor | düşük | Bir istek harici bir hizmete ulaştı; hiçbir ödeme yapılmadı |
| [`wallet_limit_exceeded`](#wallet-limit-exceeded) | Harcama sınırı aşıldı | orta | Hiçbir şey gönderilmedi, imzalanmadı veya değiştirilmedi |
| [`wallet_not_found`](#wallet-not-found) | Cüzdan, öneri veya alıcı tanınmadı | düşük | Hiçbir şey gönderilmedi, imzalanmadı veya değiştirilmedi |
| [`wallet_password_invalid`](#wallet-password-invalid) | Cüzdan parolası geçersiz | düşük | Hiçbir şey gönderilmedi, imzalanmadı veya değiştirilmedi |
| [`wallet_paykit_unavailable`](#wallet-paykit-unavailable) | Solana pay eki kurulu değil | orta | Hiçbir şey gönderilmedi, imzalanmadı veya değiştirilmedi |
| [`wallet_payment_channel_off`](#wallet-payment-channel-off) | Ödeme kanalı kapalı | düşük | Hiçbir şey gönderilmedi, imzalanmadı veya değiştirilmedi |
| [`wallet_pin_invalid`](#wallet-pin-invalid) | PIN geçersiz | düşük | Hiçbir şey gönderilmedi, imzalanmadı veya değiştirilmedi |
| [`wallet_quote_expired`](#wallet-quote-expired) | Önizlemenin süresi doldu veya değiştirildi | düşük | Hiçbir şey gönderilmedi, imzalanmadı veya değiştirilmedi |
| [`wallet_recipient_refused`](#wallet-recipient-refused) | Alıcı bunu güvenle alamaz | orta | Hiçbir şey gönderilmedi, imzalanmadı veya değiştirilmedi |
| [`wallet_request_ambiguous`](#wallet-request-ambiguous) | Birden fazla hesap veya ağ uyuyor | düşük | Hiçbir şey gönderilmedi, imzalanmadı veya değiştirilmedi |
| [`wallet_send_needs_vool_wallet`](#wallet-send-needs-vool-wallet) | Göndermek için bir VOOL Wallet gerekir | düşük | Hiçbir şey gönderilmedi, imzalanmadı veya değiştirilmedi |
| [`wallet_setup_state_invalid`](#wallet-setup-state-invalid) | Kurulum adımı şu anda uygulanamaz | düşük | Hiçbir şey gönderilmedi, imzalanmadı veya değiştirilmedi |
| [`wallet_signing_unavailable`](#wallet-signing-unavailable) | Cüzdan buradan imzalayamaz | orta | Hiçbir şey gönderilmedi, imzalanmadı veya değiştirilmedi |
| [`wallet_storage_class_refused`](#wallet-storage-class-refused) | Anahtar saklama yalnızca bellekte | orta | Hiçbir şey gönderilmedi, imzalanmadı veya değiştirilmedi |
| [`wallet_x402_cap_exceeded`](#wallet-x402-cap-exceeded) | Ücretli kaynak otomatik ödeme tavanının üzerinde | orta | Hiçbir şey gönderilmedi, imzalanmadı veya değiştirilmedi |
| [`wallet_x402_sdk_unavailable`](#wallet-x402-sdk-unavailable) | x402 eki kurulu değil | orta | Hiçbir şey gönderilmedi, imzalanmadı veya değiştirilmedi |
| [`x402_scheme_unavailable`](#x402-scheme-unavailable) | Ödeme şeması desteklenmiyor | düşük | Hiçbir şey gönderilmedi, imzalanmadı veya değiştirilmedi |

## Erişilebilirlik

| Kod | Başlık | Önem derecesi | Zaten ne olmuş olabilir |
|---|---|---|---|
| [`provider_exhausted`](#provider-exhausted) | Sağlayıcı sınırına ulaşıldı | orta | Bir istek harici bir hizmete ulaştı; hiçbir ödeme yapılmadı |
| [`provider_unavailable`](#provider-unavailable) | Sağlayıcıya ulaşılamıyor | orta | Hiçbir şey gönderilmedi, imzalanmadı veya değiştirilmedi |
| [`timeout`](#timeout) | Zaman aşımı | orta | Sonuç bilinmiyor — tekrar denemeden önce durumu kontrol edin |
| [`tool_unavailable`](#tool-unavailable) | Araç kullanılamıyor | düşük | Hiçbir şey gönderilmedi, imzalanmadı veya değiştirilmedi |
| [`wallet_broadcast_failed`](#wallet-broadcast-failed) | Ödeme yayınlanamadı | orta | Sonuç bilinmiyor — tekrar denemeden önce durumu kontrol edin |
| [`wallet_device_auth_unavailable`](#wallet-device-auth-unavailable) | Cihazla kilit açma kullanılamıyor | orta | Hiçbir şey gönderilmedi, imzalanmadı veya değiştirilmedi |
| [`wallet_quote_unavailable`](#wallet-quote-unavailable) | Bakiye veya ücret bilinmiyor | orta | Bir istek harici bir hizmete ulaştı; hiçbir ödeme yapılmadı |
| [`wallet_simulation_failed`](#wallet-simulation-failed) | Ödeme simülasyondan geçemedi | orta | Bir istek harici bir hizmete ulaştı; hiçbir ödeme yapılmadı |

## İptal

| Kod | Başlık | Önem derecesi | Zaten ne olmuş olabilir |
|---|---|---|---|
| [`cancelled`](#cancelled) | İptal edildi | bilgi | Sonuç bilinmiyor — tekrar denemeden önce durumu kontrol edin |

## Dahili

| Kod | Başlık | Önem derecesi | Zaten ne olmuş olabilir |
|---|---|---|---|
| [`unknown`](#unknown) | Beklenmeyen hata | orta | Sonuç bilinmiyor — tekrar denemeden önce durumu kontrol edin |

## Koda göre başvuru

Tüm kodlar, alfabetik sırayla. Bölüm başlığı, kodun sabit çapasıdır.

<a name="cancelled"></a>

### cancelled

**İptal edildi**

- **Ne oldu:** Bu iptal edildi, bu yüzden tamamlanmadı.
- **Zaten ne olmuş olabilir:** Sonuç bilinmiyor — tekrar denemeden önce durumu kontrol edin
- **Sonra ne yapılmalı:** Bir şey gerekmez; yapılmış olmasını istiyorsanız yeniden başlayın.
- **Tekrar denemek:** Aynı isteği değiştirmeden yeniden göndermeyin.
- **Önem derecesi:** bilgi · **Kategori:** iptal

<a name="confinement-refusal"></a>

### confinement_refusal

**İzin verilen kapsam dışında değişiklik**

- **Ne oldu:** İzin verilen kapsam dışındaki bir dosya değişikliği engellendi. Kapsam dışında hiçbir şeye dokunulmadı.
- **Zaten ne olmuş olabilir:** Hiçbir şey gönderilmedi, imzalanmadı veya değiştirilmedi
- **Sonra ne yapılmalı:** Bu değişiklik isteniyorsa, yetkili kapsam içinde olması için açıkça isteyin.
- **Tekrar denemek:** Aynı isteği değiştirmeden yeniden göndermeyin.
- **Önem derecesi:** yüksek · **Kategori:** güvenlik · **Güvenlikle ilgili:** evet

<a name="credential-failure"></a>

### credential_failure

**Saklı kimlik bilgisi okunamadı**

- **Ne oldu:** Saklı bir kimlik bilgisi okunamadı. Devam etmek için ayarlardan yeniden girin.
- **Zaten ne olmuş olabilir:** Hiçbir şey gönderilmedi, imzalanmadı veya değiştirilmedi
- **Sonra ne yapılmalı:** Kasa girdisinin şifresi çözülemedi; kimlik bilgisini yeniden saklayın ya da kasa anahtarını kontrol edin.
- **Tekrar denemek:** Aynı isteği değiştirmeden yeniden göndermeyin.
- **Önem derecesi:** yüksek · **Kategori:** güvenlik · **Güvenlikle ilgili:** evet

<a name="evidence-corruption"></a>

### evidence_corruption

**Yanıt kayıtları tutarlılık denetiminden geçemedi**

- **Ne oldu:** Bu yanıtın dayandığı kayıtlar bir tutarlılık denetiminden geçemedi, yanıtı doğrulanmamış sayın.
- **Zaten ne olmuş olabilir:** Yerel bir kayıt veya değişiklik yapıldı
- **Sonra ne yapılmalı:** Bu turun yürütme defterini çalışma zamanının olay akışına karşı inceleyin.
- **Tekrar denemek:** Aynı isteği değiştirmeden yeniden göndermeyin.
- **Önem derecesi:** yüksek · **Kategori:** bütünlük · **Güvenlikle ilgili:** evet

<a name="evm-pocket-custody-unavailable"></a>

### evm_pocket_custody_unavailable

**Eski cep yalnızca Solana için**

- **Ne oldu:** Eski cep, yani kurtarma ifadesine dayalı cüzdan, yalnızca bir Solana anahtarı tutar, bu yüzden bir EVM ağında oluşturulamaz veya geri yüklenemez. Hiçbir şey oluşturulmadı. Bir EVM ağı için Ayarlar, Kripto bölümünde o ağda bir VOOL Cüzdanı oluşturun, hesabı yalnızca izleme amaçlı ekleyin veya kendi EVM cüzdanınızı bağlayın.
- **Zaten ne olmuş olabilir:** Hiçbir şey gönderilmedi, imzalanmadı veya değiştirilmedi
- **Sonra ne yapılmalı:** Eski cep, kurtarma ifadesinden tek bir Solana tohumu mühürler. Bir EVM ağında bir Crypto Pilot cüzdanı (Ayarlar, Kripto, Ağlar ve cüzdanlar bölümünde o ağın "Create wallet" düğmesi), yalnızca izleme amaçlı bir hesap veya kendi anahtarını kendisi tutan harici bir EVM cüzdanı (EIP-1193) kullanın.
- **Tekrar denemek:** Aynı isteği değiştirmeden yeniden göndermeyin.
- **Önem derecesi:** düşük · **Kategori:** ilke

<a name="integrity-verification-failure"></a>

### integrity_verification_failure

**Kayıt bütünlük denetiminden geçemedi**

- **Ne oldu:** Bir kayıt bütünlük denetiminden geçemedi. Hiçbir şey sessizce kabul edilmedi.
- **Zaten ne olmuş olabilir:** Yerel bir kayıt veya değişiklik yapıldı
- **Sonra ne yapılmalı:** Etkilenen makbuz zincirini güvenliği bozulmuş sayın; girdilerine güvenmeden önce inceleyin.
- **Tekrar denemek:** Aynı isteği değiştirmeden yeniden göndermeyin.
- **Önem derecesi:** kritik · **Kategori:** bütünlük · **Güvenlikle ilgili:** evet

<a name="permission-denied"></a>

### permission_denied

**İzin reddedildi**

- **Ne oldu:** O eyleme izin verilmedi, bu yüzden hiçbir şey değişmedi.
- **Zaten ne olmuş olabilir:** Hiçbir şey gönderilmedi, imzalanmadı veya değiştirilmedi
- **Sonra ne yapılmalı:** Bu eylemi yöneten izin ilkesini veya onay ayarlarını kontrol edin.
- **Tekrar denemek:** Aynı isteği değiştirmeden yeniden göndermeyin.
- **Önem derecesi:** orta · **Kategori:** ilke · **Güvenlikle ilgili:** evet

<a name="provider-exhausted"></a>

### provider_exhausted

**Sağlayıcı sınırına ulaşıldı**

- **Ne oldu:** Model sağlayıcısının kullanım sınırına ulaşıldı. Daha sonra tekrar deneyebilirsiniz.
- **Zaten ne olmuş olabilir:** Bir istek harici bir hizmete ulaştı; hiçbir ödeme yapılmadı
- **Sonra ne yapılmalı:** Yapılandırılmış sağlayıcının kotasını, hız sınırlarını ve ödemesini kontrol edin.
- **Tekrar denemek:** Daha sonra tekrar deneyin: bunu durduran şey zamanla ortadan kalkar.
- **Önem derecesi:** orta · **Kategori:** erişilebilirlik

<a name="provider-unavailable"></a>

### provider_unavailable

**Sağlayıcıya ulaşılamıyor**

- **Ne oldu:** Model sağlayıcısına ulaşılamadı. Birazdan tekrar deneyebilirsiniz.
- **Zaten ne olmuş olabilir:** Hiçbir şey gönderilmedi, imzalanmadı veya değiştirilmedi
- **Sonra ne yapılmalı:** Sağlayıcının durumunu, ağ erişilebilirliğini ve yapılandırılmış uçları kontrol edin.
- **Tekrar denemek:** Daha sonra tekrar deneyin: bunu durduran şey zamanla ortadan kalkar.
- **Önem derecesi:** orta · **Kategori:** erişilebilirlik

<a name="timeout"></a>

### timeout

**Zaman aşımı**

- **Ne oldu:** Bu çok uzun sürdü ve durduruldu. Tekrar denemek işe yarayabilir.
- **Zaten ne olmuş olabilir:** Sonuç bilinmiyor — tekrar denemeden önce durumu kontrol edin
- **Sonra ne yapılmalı:** Zaman aşımları yineleniyorsa ağ gecikmesini ve sağlayıcının yanıt hızını kontrol edin.
- **Tekrar denemek:** Şimdi tekrar denemek işe yarayabilir.
- **Önem derecesi:** orta · **Kategori:** erişilebilirlik

<a name="tool-unavailable"></a>

### tool_unavailable

**Araç kullanılamıyor**

- **Ne oldu:** O araç şu anda kullanılamıyor, bu yüzden bu kısım yapılmadı. Tekrar denemeden önce etkinleştirilmesi gerekir.
- **Zaten ne olmuş olabilir:** Hiçbir şey gönderilmedi, imzalanmadı veya değiştirilmedi
- **Sonra ne yapılmalı:** Aracı ayarlardan bu mod için etkinleştirin ya da başka bir yol seçin.
- **Tekrar denemek:** Bu girdinin belirttiği değişikliği yaptıktan sonra tekrar deneyin.
- **Önem derecesi:** düşük · **Kategori:** erişilebilirlik

<a name="unknown"></a>

### unknown

**Beklenmeyen hata**

- **Ne oldu:** Bunu işlerken bu makinede bir terslik oldu. Tekrar deneyebilirsiniz.
- **Zaten ne olmuş olabilir:** Sonuç bilinmiyor — tekrar denemeden önce durumu kontrol edin
- **Sonra ne yapılmalı:** Hatanın sansürlü neden zincirini tanılamada görün; bilinmeyen neden asla körü körüne yinelenmez.
- **Tekrar denemek:** Aynı isteği değiştirmeden yeniden göndermeyin.
- **Önem derecesi:** orta · **Kategori:** dahili

<a name="unsupported-claim"></a>

### unsupported_claim

**Desteklenmeyen ifadeler tutuldu**

- **Ne oldu:** Bir veya daha fazla ifade bulunan kaynaklarca desteklenmediği için tutuldu.
- **Zaten ne olmuş olabilir:** Yerel bir kayıt veya değişiklik yapıldı
- **Sonra ne yapılmalı:** Bir şey gerekmez — temellendirme kapısı işini yapıyor. Sık oluyorsa arama kalitesini kontrol edin.
- **Tekrar denemek:** Aynı isteği değiştirmeden yeniden göndermeyin.
- **Önem derecesi:** orta · **Kategori:** bütünlük

<a name="wallet-acknowledgement-required"></a>

### wallet_acknowledgement_required

**Onay gerekli**

- **Ne oldu:** Bu adım önce onayınızı gerektiriyor, bu yüzden hiçbir şey imzalanmadı veya gönderilmedi. Cüzdanın bu işlemin ne yaptığı hakkında söylediğini okuyun, tam olarak onaylayın, sonra tekrar deneyin.
- **Zaten ne olmuş olabilir:** Hiçbir şey gönderilmedi, imzalanmadı veya değiştirilmedi
- **Sonra ne yapılmalı:** Bir onay kalıcı harcama hakları verecek, sahipliği değiştirecek veya bir yükseltme çalıştıracaksa ya da cüzdan bir uyuşmazlık bulursa veya isteği okuyamazsa, önce tam onay metnini yazmanızı ister; yerine genel hiçbir şey geçmez.
- **Tekrar denemek:** Bu girdinin belirttiği değişikliği yaptıktan sonra tekrar deneyin.
- **Önem derecesi:** düşük · **Kategori:** ilke

<a name="wallet-amount-invalid"></a>

### wallet_amount_invalid

**Miktar bu coin için kesin değil**

- **Ne oldu:** O miktar bu coin'in tutabileceği kesin bir miktar değil, bu yüzden hiçbir şey önerilmedi. Miktarı kontrol edip tekrar deneyin.
- **Zaten ne olmuş olabilir:** Hiçbir şey gönderilmedi, imzalanmadı veya değiştirilmedi
- **Sonra ne yapılmalı:** Düz bir ondalık miktar gönderin, coin'in izin verdiğinden daha hassas olmasın ve pilotun aktarım başına sınırı içinde olsun.
- **Tekrar denemek:** Bu girdinin belirttiği değişikliği yaptıktan sonra tekrar deneyin.
- **Önem derecesi:** düşük · **Kategori:** ilke

<a name="wallet-approval-rejected"></a>

### wallet_approval_rejected

**Onay kabul edilmedi**

- **Ne oldu:** Bu ödemenin onayı kabul edilmedi, bu yüzden hiçbir şey imzalanmadı veya gönderilmedi.
- **Zaten ne olmuş olabilir:** Hiçbir şey gönderilmedi, imzalanmadı veya değiştirilmedi
- **Sonra ne yapılmalı:** Tam parametreleri sahibin PIN'i veya biyometrisiyle onaylayın; onaylar parametreler arasında taşınmaz.
- **Tekrar denemek:** Aynı isteği değiştirmeden yeniden göndermeyin.
- **Önem derecesi:** orta · **Kategori:** ilke · **Güvenlikle ilgili:** evet

<a name="wallet-backup-unavailable"></a>

### wallet_backup_unavailable

**Yedek zaten bir kez gösterildi**

- **Ne oldu:** Bu cüzdanın yedek anahtarı bir kez gösterildi, tekrar gösterilemez. Kaydetmediyseniz kurulumu iptal edin ve bu adrese fon göndermeyin.
- **Zaten ne olmuş olabilir:** Hiçbir şey gönderilmedi, imzalanmadı veya değiştirilmedi
- **Sonra ne yapılmalı:** Kripto Pilot yedeği kurulum sırasında tam bir kez açıklanır; ikinci açıklama ve dışa aktarma yoktur.
- **Tekrar denemek:** Aynı isteği değiştirmeden yeniden göndermeyin.
- **Önem derecesi:** yüksek · **Kategori:** güvenlik · **Güvenlikle ilgili:** evet

<a name="wallet-broadcast-failed"></a>

### wallet_broadcast_failed

**Ödeme yayınlanamadı**

- **Ne oldu:** Ödeme yayınlanamadı. Tekrar denemeden önce durumunu kontrol edin.
- **Zaten ne olmuş olabilir:** Sonuç bilinmiyor — tekrar denemeden önce durumu kontrol edin
- **Sonra ne yapılmalı:** Ağın RPC uç noktasını ve önerinin makbuzunu kontrol edin; harcama gönderildi olarak kaydedilmedi.
- **Tekrar denemek:** Daha sonra tekrar deneyin: bunu durduran şey zamanla ortadan kalkar.
- **Önem derecesi:** orta · **Kategori:** erişilebilirlik

<a name="wallet-caller-refused"></a>

### wallet_caller_refused

**VOOL'un kendi penceresinden başlatılmadı**

- **Ne oldu:** Bu cüzdan eylemi yalnızca bu bilgisayardaki VOOL'un kendi penceresinden başlatılabilir, bu yüzden hiçbir şey değişmedi, imzalanmadı veya gönderilmedi. VOOL'u açın ve orada tekrar deneyin.
- **Zaten ne olmuş olabilir:** Hiçbir şey gönderilmedi, imzalanmadı veya değiştirilmedi
- **Sonra ne yapılmalı:** Güvenilir cüzdan kapılarını sunulan sayfadan ya da yerel pencereden kullanın; başka bir kökenden sayfa, çerçeve veya eklenti reddedilir.
- **Tekrar denemek:** Bu girdinin belirttiği değişikliği yaptıktan sonra tekrar deneyin.
- **Önem derecesi:** yüksek · **Kategori:** güvenlik · **Güvenlikle ilgili:** evet

<a name="wallet-card-data-refused"></a>

### wallet_card_data_refused

**Kart verisi reddedildi**

- **Ne oldu:** Kart numaraları ve güvenlik kodları burada asla saklanmaz; yalnızca sağlayıcı jetonu kabul edilir. Hiçbir şey kaydedilmedi.
- **Zaten ne olmuş olabilir:** Hiçbir şey gönderilmedi, imzalanmadı veya değiştirilmedi
- **Sonra ne yapılmalı:** Kartı sağlayıcıyla jetonlaştırın ve jeton başvurusunu kaydedin.
- **Tekrar denemek:** Aynı isteği değiştirmeden yeniden göndermeyin.
- **Önem derecesi:** yüksek · **Kategori:** güvenlik · **Güvenlikle ilgili:** evet

<a name="wallet-chain-identity-mismatch"></a>

### wallet_chain_identity_mismatch

**Zincir ucunun kimliği kanıtlanmadı**

- **Ne oldu:** Zincir ucu iddia ettiği ağın kimliğini kanıtlamadı, bu yüzden hiçbir şey imzalanmadı veya gönderilmedi.
- **Zaten ne olmuş olabilir:** Bir istek harici bir hizmete ulaştı; hiçbir ödeme yapılmadı
- **Sonra ne yapılmalı:** Ağın RPC ucunu kontrol edin; yalan söyleyen zincir kimliği ya da genesis, herhangi bir ödemeden önce reddedilir.
- **Tekrar denemek:** Aynı isteği değiştirmeden yeniden göndermeyin.
- **Önem derecesi:** yüksek · **Kategori:** güvenlik · **Güvenlikle ilgili:** evet

<a name="wallet-confirmation-required"></a>

### wallet_confirmation_required

**Yazılı onay gerekli**

- **Ne oldu:** O adım önce yazılı onayı gerektiriyor, bu yüzden hiçbir şey değişmedi. Onaylayın, sonra tekrar deneyin.
- **Zaten ne olmuş olabilir:** Hiçbir şey gönderilmedi, imzalanmadı veya değiştirilmedi
- **Sonra ne yapılmalı:** Uyarıyı okuyun ve sürmek için tam onay cümlesini yazın.
- **Tekrar denemek:** Bu girdinin belirttiği değişikliği yaptıktan sonra tekrar deneyin.
- **Önem derecesi:** düşük · **Kategori:** ilke

<a name="wallet-credential-mismatch"></a>

### wallet_credential_mismatch

**Girişler birbirini tutmadı**

- **Ne oldu:** İki giriş birbirini tutmadı, bu yüzden hiçbir şey oluşturulmadı. Aynı PIN'i veya parolayı iki kez girin, sonra tekrar deneyin.
- **Zaten ne olmuş olabilir:** Hiçbir şey gönderilmedi, imzalanmadı veya değiştirilmedi
- **Sonra ne yapılmalı:** Kripto Pilot kimlik bilgisi, herhangi bir anahtar malzemesi var olmadan önce onaylanır.
- **Tekrar denemek:** Bu girdinin belirttiği değişikliği yaptıktan sonra tekrar deneyin.
- **Önem derecesi:** düşük · **Kategori:** ilke

<a name="wallet-dependency-unavailable"></a>

### wallet_dependency_unavailable

**EVM kitaplıkları kurulu değil**

- **Ne oldu:** Bu yapı EVM imzalama veya ABI kodlaması yapamaz: Ethereum kitaplıkları bu makinede kurulu değil. Hiçbir şey imzalanmadı veya gönderilmedi. Solana, saklama ve harcama tavanları etkilenmez; kitaplıklar kurulunca tekrar deneyin.
- **Zaten ne olmuş olabilir:** Hiçbir şey gönderilmedi, imzalanmadı veya değiştirilmedi
- **Sonra ne yapılmalı:** EVM şeritlerini etkinleştirmek için EVM eklerini (eth-abi, eth-utils, eth-account) kurun; cüzdanın diğer her işlevi onsuz çalışır.
- **Tekrar denemek:** Bu girdinin belirttiği değişikliği yaptıktan sonra tekrar deneyin.
- **Önem derecesi:** orta · **Kategori:** ilke

<a name="wallet-device-auth-denied"></a>

### wallet_device_auth_denied

**Cihazla kilit açma reddedildi**

- **Ne oldu:** Touch ID veya Mac parolanız reddedildi ya da bu cüzdanı açmadı, bu yüzden hiçbir şey oluşturulmadı, değiştirilmedi, hiçbir şeyin kilidi açılmadı, hiçbir şey açığa çıkarılmadı, imzalanmadı veya gönderilmedi. Tekrar deneyin.
- **Zaten ne olmuş olabilir:** Hiçbir şey gönderilmedi, imzalanmadı veya değiştirilmedi
- **Sonra ne yapılmalı:** Tekrar deneyin ve VOOL'un gösterdiği Touch ID veya Mac parolası isteğini onaylayın. Touch ID ile onay veren bir cüzdanın bunun yerine kullanılacak kendi PIN'i veya parolası yoktur. İstek başarısız olmaya devam ederse cüzdanı kurtarma ifadesinden geri yükleyin (bir Crypto Pilot'u kurulumda gösterilen yedekten) ve ona bir PIN veya parola verin; özel anahtarın dışa aktarılması yine Touch ID veya Mac parolası gerektirir.
- **Tekrar denemek:** Bu girdinin belirttiği değişikliği yaptıktan sonra tekrar deneyin.
- **Önem derecesi:** düşük · **Kategori:** ilke

<a name="wallet-device-auth-unavailable"></a>

### wallet_device_auth_unavailable

**Cihazla kilit açma kullanılamıyor**

- **Ne oldu:** Touch ID veya Mac parolanız bu cüzdan için burada kullanılamıyor, bu yüzden hiçbir şey oluşturulmadı, değiştirilmedi, hiçbir şeyin kilidi açılmadı, hiçbir şey açığa çıkarılmadı, imzalanmadı veya gönderilmedi. Kullanılabilir olduğunda tekrar deneyin.
- **Zaten ne olmuş olabilir:** Hiçbir şey gönderilmedi, imzalanmadı veya değiştirilmedi
- **Sonra ne yapılmalı:** Yeni bir cüzdan bunun yerine kendi PIN'i veya parolasıyla onay verebilir. Touch ID ile onay veren bir cüzdanın kendi PIN'i veya parolası yoktur: Touch ID ve Mac parolası kullanılamaz kalırsa onu kurtarma ifadesinden geri yükleyin (bir Crypto Pilot'u kurulumda gösterilen yedekten) ve ona bir PIN veya parola verin. Özel anahtarın dışa aktarılması yalnızca Touch ID veya Mac parolasıyla açılır; bu yüzden cüzdanı bunların çalıştığı bir Mac'te geri yükleyin ve orada dışa aktarın.
- **Tekrar denemek:** Bu girdinin belirttiği değişikliği yaptıktan sonra tekrar deneyin.
- **Önem derecesi:** orta · **Kategori:** erişilebilirlik

<a name="wallet-disabled"></a>

### wallet_disabled

**Cüzdan kapalı**

- **Ne oldu:** Cüzdan kapalı, bu yüzden önerilen veya gönderilen ödeme olmadı. Ayarlar, Kripto bölümünde "Kripto'yu etkinleştir" anahtarını açın, sonra tekrar deneyin.
- **Zaten ne olmuş olabilir:** Hiçbir şey gönderilmedi, imzalanmadı veya değiştirilmedi
- **Sonra ne yapılmalı:** "Kripto'yu etkinleştir" anahtarını Ayarlar, Kripto bölümünde yalnızca bu kurulumda ödeme istiyorsanız açın. VOOL_WALLET_ENABLED değişkeni ayarlıysa, anahtar ne gösterirse göstersin cüzdanı o açar veya kapatır.
- **Tekrar denemek:** Bu girdinin belirttiği değişikliği yaptıktan sonra tekrar deneyin.
- **Önem derecesi:** düşük · **Kategori:** ilke

<a name="wallet-duplicate-payment"></a>

### wallet_duplicate_payment

**Ödeme zaten önerildi veya gönderildi**

- **Ne oldu:** Bu ödeme zaten önerilmişti veya gönderilmişti, bu yüzden tekrar gönderilmedi.
- **Zaten ne olmuş olabilir:** Yerel bir kayıt veya değişiklik yapıldı
- **Sonra ne yapılmalı:** Var olan önerinin makbuzunu kontrol edin; gerçekten yeni bir ödeme için yeni bir eşgüçlülük anahtarı kullanın.
- **Tekrar denemek:** Aynı isteği değiştirmeden yeniden göndermeyin.
- **Önem derecesi:** orta · **Kategori:** bütünlük · **Güvenlikle ilgili:** evet

<a name="wallet-environment-inactive"></a>

### wallet_environment_inactive

**Yanlış ağ ortamı**

- **Ne oldu:** O ağ diğer ağ ortamına (Mainnet veya Test ağları) ait, bu yüzden hiçbir şey oluşturulmadı, önerilmedi veya imzalanmadı. Ayarlar, Kripto, Geliştirici seçenekleri'nden geçin, sonra tekrar deneyin.
- **Zaten ne olmuş olabilir:** Hiçbir şey gönderilmedi, imzalanmadı veya değiştirilmedi
- **Sonra ne yapılmalı:** Ağ ortamını Kripto ayarlarında bilinçli seçin; hesaplar, bakiyeler ve makbuzlar ortamlar arasında asla taşınmaz.
- **Tekrar denemek:** Bu girdinin belirttiği değişikliği yaptıktan sonra tekrar deneyin.
- **Önem derecesi:** orta · **Kategori:** güvenlik · **Güvenlikle ilgili:** evet

<a name="wallet-export-pin-not-accepted"></a>

### wallet_export_pin_not_accepted

**Dışa aktarma yalnızca cihaz kimlik doğrulamasıyla açılır**

- **Ne oldu:** Dışa aktarma hiçbir zaman PIN, parola veya parola ifadesiyle açılmaz, yalnızca cihaz kimlik doğrulamasıyla (Touch ID veya Mac parolanız) açılır, bu yüzden hiçbir şey açığa çıkarılmadı. PIN veya parola olmadan tekrar deneyin.
- **Zaten ne olmuş olabilir:** Hiçbir şey gönderilmedi, imzalanmadı veya değiştirilmedi
- **Sonra ne yapılmalı:** Dışa aktarma bilerek yalnızca cihaz kimlik doğrulamasıyla açılır; istekteki bir PIN, parola veya parola ifadesi daha kontrol edilmeden reddedilir.
- **Tekrar denemek:** Bu girdinin belirttiği değişikliği yaptıktan sonra tekrar deneyin.
- **Önem derecesi:** yüksek · **Kategori:** güvenlik · **Güvenlikle ilgili:** evet

<a name="wallet-export-refused"></a>

### wallet_export_refused

**Eski dışa aktarma yolu kullanımdan kaldırıldı**

- **Ne oldu:** Özel anahtarı bu yolla dışa aktarma kullanımdan kaldırıldı, bu yüzden hiçbir şey açığa çıkarılmadı. Dışa aktarmaya izin veren bir cüzdanın Ayarlar, Kripto bölümünde "Export private key" düğmesi vardır ve yalnızca Touch ID veya Mac parolanızla açılır.
- **Zaten ne olmuş olabilir:** Hiçbir şey gönderilmedi, imzalanmadı veya değiştirilmedi
- **Sonra ne yapılmalı:** Ayarlar, Kripto, Diğer hesaplar bölümündeki "Export private key" düğmesini kullanın: yalnızca eski cep dışa aktarılır, o da yalnızca Touch ID veya Mac parolasıyla. Bir Crypto Pilot cüzdanı asla dışa aktarılmaz; kurulumda gösterilen yedekten kurtarılır.
- **Tekrar denemek:** Aynı isteği değiştirmeden yeniden göndermeyin.
- **Önem derecesi:** yüksek · **Kategori:** güvenlik · **Güvenlikle ilgili:** evet

<a name="wallet-export-unavailable"></a>

### wallet_export_unavailable

**Bu cüzdan için dışa aktarma kullanılamıyor**

- **Ne oldu:** Bu cüzdan bu şekilde dışa aktarılamaz: yalnızca izleme amaçlıdır veya bu makinede anahtar tutmaz, tek yedeği kurulumda gösterilmiştir ve asla dışa aktarılmaz, VOOL seçtiğiniz cüzdan uygulamasına dışa aktarmaz ya da MetaMask'e dışa aktarılmadan önce kurtarma ifadesinden geri yüklenmesi gereken, önceki bir sürümden kalma bir eski ceptir. Hiçbir şey açığa çıkarılmadı.
- **Zaten ne olmuş olabilir:** Hiçbir şey gönderilmedi, imzalanmadı veya değiştirilmedi
- **Sonra ne yapılmalı:** Yalnızca bu makinede mühürlenmiş bir eski cep dışa aktarılır, Phantom veya MetaMask'e, o da yalnızca Touch ID veya Mac parolasıyla. Bir Crypto Pilot cüzdanı, kurulumda gösterilen yedekten kurtarılır. Önceki bir sürümden kalma eski cep, kurtarma ifadesinden geri yüklendikten sonra MetaMask'e aktarılır.
- **Tekrar denemek:** Aynı isteği değiştirmeden yeniden göndermeyin.
- **Önem derecesi:** yüksek · **Kategori:** güvenlik · **Güvenlikle ilgili:** evet

<a name="wallet-insufficient-funds"></a>

### wallet_insufficient_funds

**Bakiye miktarı ve ücreti karşılamıyor**

- **Ne oldu:** Bakiye miktarı artı bu ağ ücretinin en fazla olabileceğini karşılamıyor, bu yüzden hiçbir şey hazırlanmadı. Miktarı düşürün veya fon ekleyin, sonra tekrar deneyin.
- **Zaten ne olmuş olabilir:** Bir istek harici bir hizmete ulaştı; hiçbir ödeme yapılmadı
- **Sonra ne yapılmalı:** Harcanabilir, süren diğer ayırmaları dışlar; bir Solana kalanı sıfırsız bitmeli ve en az kirayı karşılamalı.
- **Tekrar denemek:** Bu girdinin belirttiği değişikliği yaptıktan sonra tekrar deneyin.
- **Önem derecesi:** düşük · **Kategori:** ilke

<a name="wallet-legacy-surface-retired"></a>

### wallet_legacy_surface_retired

**Eski para yolu kullanımdan kaldırıldı**

- **Ne oldu:** O para yolu emekliye ayrıldı: her ödeme cüzdanın öneri ve onay akışından geçer. Hiçbir şey imzalanmadı veya gönderilmedi.
- **Zaten ne olmuş olabilir:** Hiçbir şey gönderilmedi, imzalanmadı veya değiştirilmedi
- **Sonra ne yapılmalı:** Cüzdan araçlarını (wallet.propose) ya da cüzdan API'sini kullanın; eski imzalayıcı, harcayan ve dışa aktarıcı artık hizmet vermiyor.
- **Tekrar denemek:** Aynı isteği değiştirmeden yeniden göndermeyin.
- **Önem derecesi:** yüksek · **Kategori:** güvenlik · **Güvenlikle ilgili:** evet

<a name="wallet-limit-exceeded"></a>

### wallet_limit_exceeded

**Harcama sınırı aşıldı**

- **Ne oldu:** O ödeme bir harcama sınırının üzerinde, bu yüzden gönderilmedi.
- **Zaten ne olmuş olabilir:** Hiçbir şey gönderilmedi, imzalanmadı veya değiştirilmedi
- **Sonra ne yapılmalı:** Yeniden denemeden önce işlem başına, günlük ve alıcı başına sınırları gözden geçirin.
- **Tekrar denemek:** Aynı isteği değiştirmeden yeniden göndermeyin.
- **Önem derecesi:** orta · **Kategori:** ilke · **Güvenlikle ilgili:** evet

<a name="wallet-network-disabled"></a>

### wallet_network_disabled

**Ağ bu cüzdan için bildirilmemiş**

- **Ne oldu:** O ağ bu cüzdanın bu eylem için bildirdiği ağlardan değil, bu yüzden hiçbir şey oluşturulmadı veya gönderilmedi.
- **Zaten ne olmuş olabilir:** Hiçbir şey gönderilmedi, imzalanmadı veya değiştirilmedi
- **Sonra ne yapılmalı:** Bildirilen ağ satırlarından birini kullanın; bildirilmemiş zincirler, takma adlar ve benzer isimler sicilce reddedilir.
- **Tekrar denemek:** Aynı isteği değiştirmeden yeniden göndermeyin.
- **Önem derecesi:** yüksek · **Kategori:** güvenlik · **Güvenlikle ilgili:** evet

<a name="wallet-not-found"></a>

### wallet_not_found

**Cüzdan, öneri veya alıcı tanınmadı**

- **Ne oldu:** O cüzdan, öneri veya alıcı tanınmadı, bu yüzden hiçbir şey yapılmadı. Kontrol edin ve tekrar deneyin.
- **Zaten ne olmuş olabilir:** Hiçbir şey gönderilmedi, imzalanmadı veya değiştirilmedi
- **Sonra ne yapılmalı:** Cüzdan kimliğini, öneri kimliğini veya alıcı adresini kontrol edip tekrar deneyin.
- **Tekrar denemek:** Bu girdinin belirttiği değişikliği yaptıktan sonra tekrar deneyin.
- **Önem derecesi:** düşük · **Kategori:** ilke

<a name="wallet-outbound-refused"></a>

### wallet_outbound_refused

**İstek onaylı uçların dışında**

- **Ne oldu:** O isteğe cüzdanın onaylı uçlarından ayrılma izni verilmedi, bu yüzden bağlantı kurulmadı.
- **Zaten ne olmuş olabilir:** Hiçbir şey gönderilmedi, imzalanmadı veya değiştirilmedi
- **Sonra ne yapılmalı:** Bildirilen RPC kökenlerini ve ödeme kaynağı ilkesini kontrol edin; özel, üstveri ve onaylanmamış düğümler reddedilir.
- **Tekrar denemek:** Aynı isteği değiştirmeden yeniden göndermeyin.
- **Önem derecesi:** yüksek · **Kategori:** güvenlik · **Güvenlikle ilgili:** evet

<a name="wallet-password-invalid"></a>

### wallet_password_invalid

**Cüzdan parolası geçersiz**

- **Ne oldu:** Parola politikaya uymadı veya cüzdanın kilidini açmadı, bu yüzden hiçbir şey imzalanmadı ya da değiştirilmedi. Parolayı kontrol edin ve tekrar deneyin.
- **Zaten ne olmuş olabilir:** Hiçbir şey gönderilmedi, imzalanmadı veya değiştirilmedi
- **Sonra ne yapılmalı:** Cüzdan parolaları 10 ile 128 karakter arasındadır ve yalnızca rakamlardan oluşmaz. Yanlış bir parola anahtarı mühürlü bırakır ve çok fazla yanlış denemeden sonraki cüzdan kilidine sayılır.
- **Tekrar denemek:** Bu girdinin belirttiği değişikliği yaptıktan sonra tekrar deneyin.
- **Önem derecesi:** düşük · **Kategori:** ilke

<a name="wallet-paykit-unavailable"></a>

### wallet_paykit_unavailable

**Solana pay eki kurulu değil**

- **Ne oldu:** Bu yapı Solana x402 veya MPP tekliflerini ödeyemez: isteğe bağlı pay eki (Solana pay-kit, Python 3.11 veya daha yenisi) bu makinede kurulu değil. Hiçbir şey imzalanmadı veya gönderilmedi. Cüzdanın diğer her işlevi onsuz çalışır; kurulunca tekrar deneyin.
- **Zaten ne olmuş olabilir:** Hiçbir şey gönderilmedi, imzalanmadı veya değiştirilmedi
- **Sonra ne yapılmalı:** pay-kit ödemelerini etkinleştirmek için VOOL'un isteğe bağlı pay ekini Python 3.11 veya daha yenisine kurun; cüzdan her durumda kendi imzalayıcısını, harcama tavanlarını ve onayını korur.
- **Tekrar denemek:** Bu girdinin belirttiği değişikliği yaptıktan sonra tekrar deneyin.
- **Önem derecesi:** orta · **Kategori:** ilke

<a name="wallet-payment-channel-off"></a>

### wallet_payment_channel_off

**Ödeme kanalı kapalı**

- **Ne oldu:** Bu ödeme kapalı bir ödeme kanalından geçiyor, bu yüzden hiçbir şey imzalanmadı veya gönderilmedi. Kanalı Ayarlar, Kripto, Ödeme kanalları'ndan açın, sonra tekrar deneyin.
- **Zaten ne olmuş olabilir:** Hiçbir şey gönderilmedi, imzalanmadı veya değiştirilmedi
- **Sonra ne yapılmalı:** Bir ödeme kanalını Kripto ayarlarında yalnızca ödemelerin onun üzerinden yapılmasını istiyorsanız açın; tavan ve sizin onayınız her kanalda geçerlidir.
- **Tekrar denemek:** Bu girdinin belirttiği değişikliği yaptıktan sonra tekrar deneyin.
- **Önem derecesi:** düşük · **Kategori:** ilke

<a name="wallet-pin-invalid"></a>

### wallet_pin_invalid

**PIN geçersiz**

- **Ne oldu:** PIN politikaya uymadı veya cüzdanı açmadı, bu yüzden hiçbir şey imzalanmadı. PIN'i kontrol edin ve tekrar deneyin.
- **Zaten ne olmuş olabilir:** Hiçbir şey gönderilmedi, imzalanmadı veya değiştirilmedi
- **Sonra ne yapılmalı:** 6–12 haneli bir PIN kullanın; yanlış PIN mühürlü anahtara dokunmaz.
- **Tekrar denemek:** Bu girdinin belirttiği değişikliği yaptıktan sonra tekrar deneyin.
- **Önem derecesi:** düşük · **Kategori:** ilke

<a name="wallet-quote-expired"></a>

### wallet_quote_expired

**Önizlemenin süresi doldu veya değiştirildi**

- **Ne oldu:** Bu transfer önizlemesinin süresi doldu veya değiştirildi, bu yüzden onaylanamaz ve hiçbir şey gönderilmedi. Önizlemeyi yenileyin, sonra tekrar deneyin.
- **Zaten ne olmuş olabilir:** Hiçbir şey gönderilmedi, imzalanmadı veya değiştirilmedi
- **Sonra ne yapılmalı:** Bir teklif kısa yaşar; yenileme ya da ortam değişimi geçersiz kılar; onay her zaman geçerli olanı bağlar.
- **Tekrar denemek:** Bu girdinin belirttiği değişikliği yaptıktan sonra tekrar deneyin.
- **Önem derecesi:** düşük · **Kategori:** ilke

<a name="wallet-quote-mismatch"></a>

### wallet_quote_mismatch

**Onay artık önizlemeyle uyuşmuyor**

- **Ne oldu:** Onay artık önizlenen transferle uyuşmuyor, bu yüzden hiçbir şey imzalanmadı veya gönderilmedi. Yeni bir önizlemeden başlayın.
- **Zaten ne olmuş olabilir:** Hiçbir şey gönderilmedi, imzalanmadı veya değiştirilmedi
- **Sonra ne yapılmalı:** Onay tek bir teklif özetini bağlar; değişen hesap, zincir, alıcı, miktar, ortam veya ücret yeni onay demektir.
- **Tekrar denemek:** Aynı isteği değiştirmeden yeniden göndermeyin.
- **Önem derecesi:** yüksek · **Kategori:** güvenlik · **Güvenlikle ilgili:** evet

<a name="wallet-quote-unavailable"></a>

### wallet_quote_unavailable

**Bakiye veya ücret bilinmiyor**

- **Ne oldu:** Ağ bu transferin bakiyesi veya ücreti için eksiksiz yanıt vermedi, bu yüzden onay hazırlanmadı ve hiçbir şey gönderilmedi. Birazdan tekrar deneyin.
- **Zaten ne olmuş olabilir:** Bir istek harici bir hizmete ulaştı; hiçbir ödeme yapılmadı
- **Sonra ne yapılmalı:** Bilinmeyen zincir verisi asla sıfır sayılmaz; satırın ucunu ve kimlik kanıtını kontrol edin.
- **Tekrar denemek:** Daha sonra tekrar deneyin: bunu durduran şey zamanla ortadan kalkar.
- **Önem derecesi:** orta · **Kategori:** erişilebilirlik

<a name="wallet-recipient-refused"></a>

### wallet_recipient_refused

**Alıcı bunu güvenle alamaz**

- **Ne oldu:** O alıcı bu transferi bu ağda güvenle alamaz, bu yüzden hiçbir şey hazırlanmadı. Adresi kontrol edin ve tekrar deneyin.
- **Zaten ne olmuş olabilir:** Hiçbir şey gönderilmedi, imzalanmadı veya değiştirilmedi
- **Sonra ne yapılmalı:** Reddedildi: kiraminimumunun altında yeni Solana hesabı, yürütülebilir ya da eğri dışı hesap, bozuk EIP-55 sağlama toplamı, sıfır adresi, önceden derlenmiş sözleşmeler ve sistem sözleşmeleri.
- **Tekrar denemek:** Bu girdinin belirttiği değişikliği yaptıktan sonra tekrar deneyin.
- **Önem derecesi:** orta · **Kategori:** ilke

<a name="wallet-recovery-refused"></a>

### wallet_recovery_refused

**Kurtarma kanıtı bu cüzdana uymuyor**

- **Ne oldu:** O kurtarma kanıtı bu cüzdana uymuyor, bu yüzden hiçbir şey değişmedi: kayıtlı anahtar, VOOL'un tam bu adres için gösterdiği yedek, gösterildiği biçimde olmalı. Kontrol edip tekrar deneyin ya da diğer seçenekleri kullanın.
- **Zaten ne olmuş olabilir:** Hiçbir şey gönderilmedi, imzalanmadı veya değiştirilmedi
- **Sonra ne yapılmalı:** Kurtarma, yalnızca cüzdanın kendi anahtarına çözülen bir yedekten (iki yönüyle) var olan bir cüzdanı yeniden mühürler.
- **Tekrar denemek:** Bu girdinin belirttiği değişikliği yaptıktan sonra tekrar deneyin.
- **Önem derecesi:** yüksek · **Kategori:** güvenlik · **Güvenlikle ilgili:** evet

<a name="wallet-request-ambiguous"></a>

### wallet_request_ambiguous

**Birden fazla hesap veya ağ uyuyor**

- **Ne oldu:** Bu transfere birden fazla hesap veya ağ uyuyor, bu yüzden hiçbir şey önerilmedi. Hangi hesabı veya ağı kastettiğinizi söyleyin, sonra tekrar deneyin.
- **Zaten ne olmuş olabilir:** Hiçbir şey gönderilmedi, imzalanmadı veya değiştirilmedi
- **Sonra ne yapılmalı:** Bir aktarım bir ağda tek bir hesabı adlandırır; adaylar hata bağlamında listelenir.
- **Tekrar denemek:** Bu girdinin belirttiği değişikliği yaptıktan sonra tekrar deneyin.
- **Önem derecesi:** düşük · **Kategori:** ilke

<a name="wallet-send-needs-vool-wallet"></a>

### wallet_send_needs_vool_wallet

**Göndermek için bir VOOL Wallet gerekir**

- **Ne oldu:** Bu hesap bu ağda VOOL'dan gönderemez: burada yalnızca bu cihazda oluşturulan bir VOOL Wallet gönderir. Bağlı veya yalnızca-izleme hesabı yine de alabilir ve bakiyesini gösterebilir. Hiçbir şey imzalanmadı veya gönderilmedi. Göndermek için bu cihazdaki bir VOOL Wallet'ı seçip tekrar deneyin.
- **Zaten ne olmuş olabilir:** Hiçbir şey gönderilmedi, imzalanmadı veya değiştirilmedi
- **Sonra ne yapılmalı:** Bu cihazdaki bir VOOL Wallet'tan ya da bağlı cüzdanın kendi uygulamasından gönderin; bu ağda VOOL, bağlı veya yalnızca-izleme hesabı için hiçbir şey imzalamaz.
- **Tekrar denemek:** Bu girdinin belirttiği değişikliği yaptıktan sonra tekrar deneyin.
- **Önem derecesi:** düşük · **Kategori:** ilke

<a name="wallet-setup-state-invalid"></a>

### wallet_setup_state_invalid

**Kurulum adımı şu anda uygulanamaz**

- **Ne oldu:** O kurulum adımı şu anda bu cüzdana uygulanamaz, bu yüzden hiçbir şey değişmedi. Cüzdanın kurulum durumunu kontrol edin, sonra tekrar deneyin.
- **Zaten ne olmuş olabilir:** Hiçbir şey gönderilmedi, imzalanmadı veya değiştirilmedi
- **Sonra ne yapılmalı:** Kurulum sırayla gider: üretme, yedek bekleniyor, yedek açıklandı, hazır; iptal ve sürdürme bir yedeği asla iki kez açıklamaz.
- **Tekrar denemek:** Bu girdinin belirttiği değişikliği yaptıktan sonra tekrar deneyin.
- **Önem derecesi:** düşük · **Kategori:** ilke

<a name="wallet-signature-invalid"></a>

### wallet_signature_invalid

**İmza cüzdanın anahtarıyla uyuşmadı**

- **Ne oldu:** Bu ödeme için dönen imza cüzdanın anahtarıyla uyuşmadı, bu yüzden yayınlanmadı.
- **Zaten ne olmuş olabilir:** Bir istek harici bir hizmete ulaştı; hiçbir ödeme yapılmadı
- **Sonra ne yapılmalı:** Bağlı imzalayıcıyı kontrol edin; uyuşmayan imza yayından önce reddedilir.
- **Tekrar denemek:** Aynı isteği değiştirmeden yeniden göndermeyin.
- **Önem derecesi:** yüksek · **Kategori:** güvenlik · **Güvenlikle ilgili:** evet

<a name="wallet-signing-unavailable"></a>

### wallet_signing_unavailable

**Cüzdan buradan imzalayamaz**

- **Ne oldu:** Bu cüzdan buradan imzalayamaz: yalnızca-izleme modunda ya da dış imzalayıcısı bağlı değil. Hiçbir şey gönderilmedi; bir imzalayıcı bağlayın, sonra tekrar deneyin.
- **Zaten ne olmuş olabilir:** Hiçbir şey gönderilmedi, imzalanmadı veya değiştirilmedi
- **Sonra ne yapılmalı:** Dış cüzdanı bağlayın ya da Ayarlar, Kripto bölümünde o ağda bir VOOL Cüzdanı oluşturun.
- **Tekrar denemek:** Bu girdinin belirttiği değişikliği yaptıktan sonra tekrar deneyin.
- **Önem derecesi:** orta · **Kategori:** ilke

<a name="wallet-simulation-failed"></a>

### wallet_simulation_failed

**Ödeme simülasyondan geçemedi**

- **Ne oldu:** Ödeme simülasyondan geçemedi, bu yüzden gönderilmedi. Bakiyeyi ve alıcıyı kontrol edin, sonra tekrar deneyin.
- **Zaten ne olmuş olabilir:** Bir istek harici bir hizmete ulaştı; hiçbir ödeme yapılmadı
- **Sonra ne yapılmalı:** Bakiyeyi, alıcıyı ve ağın RPC uç noktasını kontrol edin, sonra yeniden önerin.
- **Tekrar denemek:** Daha sonra tekrar deneyin: bunu durduran şey zamanla ortadan kalkar.
- **Önem derecesi:** orta · **Kategori:** erişilebilirlik

<a name="wallet-storage-class-refused"></a>

### wallet_storage_class_refused

**Anahtar saklama yalnızca bellekte**

- **Ne oldu:** Bu kurulum cihaz anahtarını yalnızca bellekte tutuyor, bu yüzden burada oluşturulan bir cüzdan yeniden başlatmadan sonra açılamazdı. Hiçbir şey oluşturulmadı. Anahtar saklamayı kalıcı bir moda geçirin, sonra tekrar deneyin.
- **Zaten ne olmuş olabilir:** Hiçbir şey gönderilmedi, imzalanmadı veya değiştirilmedi
- **Sonra ne yapılmalı:** VOOL_KEY_STORAGE_MODE ephemeral iken Kripto Pilot cüzdanları reddedilir; file ya da keyring saklaması kullanın.
- **Tekrar denemek:** Bu girdinin belirttiği değişikliği yaptıktan sonra tekrar deneyin.
- **Önem derecesi:** orta · **Kategori:** ilke

<a name="wallet-unlock-throttled"></a>

### wallet_unlock_throttled

**Başarısız kilit açma denemelerinden sonra cüzdan kilitlendi**

- **Ne oldu:** Çok fazla yanlış PIN veya parola denemesi oldu, bu yüzden bu cüzdan bir süre kilitli ve hiçbir şey açılmadı. Bekleyin, sonra tekrar deneyin.
- **Zaten ne olmuş olabilir:** Hiçbir şey gönderilmedi, imzalanmadı veya değiştirilmedi
- **Sonra ne yapılmalı:** Başarısız açmalar, cüzdan başına ve tüm uygulama için kalıcı sayılır; yeniden başlatmalarda da; kilit kendiliğinden kalkar.
- **Tekrar denemek:** Daha sonra tekrar deneyin: bunu durduran şey zamanla ortadan kalkar.
- **Önem derecesi:** yüksek · **Kategori:** güvenlik · **Güvenlikle ilgili:** evet

<a name="wallet-x402-cap-exceeded"></a>

### wallet_x402_cap_exceeded

**Ücretli kaynak otomatik ödeme tavanının üzerinde**

- **Ne oldu:** Ücretli bir kaynak otomatik ödeme tavanından fazlasını istedi, bu yüzden ödenmedi.
- **Zaten ne olmuş olabilir:** Hiçbir şey gönderilmedi, imzalanmadı veya değiştirilmedi
- **Sonra ne yapılmalı:** VOOL_WALLET_X402_CAP_MINOR'ı bilinçli yükseltin ya da kaynağı açık bir öneriyle ödeyin.
- **Tekrar denemek:** Aynı isteği değiştirmeden yeniden göndermeyin.
- **Önem derecesi:** orta · **Kategori:** ilke · **Güvenlikle ilgili:** evet

<a name="wallet-x402-sdk-unavailable"></a>

### wallet_x402_sdk_unavailable

**x402 eki kurulu değil**

- **Ne oldu:** Bu yapı x402 Foundation SDK üzerinden Base'deki x402 tekliflerini ödeyemez: isteğe bağlı x402 eki bu makinede kurulu değil. Hiçbir şey imzalanmadı veya gönderilmedi. Cüzdanın diğer her işlevi onsuz çalışır; kurulunca tekrar deneyin.
- **Zaten ne olmuş olabilir:** Hiçbir şey gönderilmedi, imzalanmadı veya değiştirilmedi
- **Sonra ne yapılmalı:** Bir Crypto Pilot cüzdanından Base'deki x402 tekliflerini ödemek için VOOL'un isteğe bağlı x402 ekini kurun; cüzdan her durumda kendi imzalayıcısını, harcama tavanlarını ve onayını korur.
- **Tekrar denemek:** Bu girdinin belirttiği değişikliği yaptıktan sonra tekrar deneyin.
- **Önem derecesi:** orta · **Kategori:** ilke

<a name="x402-scheme-unavailable"></a>

### x402_scheme_unavailable

**Ödeme şeması desteklenmiyor**

- **Ne oldu:** Bu ödeme teklifi bu cüzdanın uçtan uca kanıtlayamayacağı bir şema gerektiriyor, bu yüzden hiçbir şey imzalanmadı. Şeması, jetonu ve aracısı bu ağ için doğrulanmış bir teklif deneyin.
- **Zaten ne olmuş olabilir:** Hiçbir şey gönderilmedi, imzalanmadı veya değiştirilmedi
- **Sonra ne yapılmalı:** Şeması, jetonu ve aracısı bu ağ için doğrulanmış bir teklif kullanın; kısmi imzalama desteği burada yeterli değildir.
- **Tekrar denemek:** Bu girdinin belirttiği değişikliği yaptıktan sonra tekrar deneyin.
- **Önem derecesi:** düşük · **Kategori:** ilke

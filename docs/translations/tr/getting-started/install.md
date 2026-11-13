---
description: Yapay zekâ asistanı VOOL'un macOS (Apple Silicon veya Intel) ya da Linux'a sürüm indirmelerinden veya macOS, Windows ya da Linux'ta kaynak koddan nasıl kurulacağı; sistem gereksinimleri ve bir indirmenin nasıl doğrulanacağı dahil.
translation: machine
source_sha256: a674148a99297acd314681d6a04bcf2e87e1c88bf00c9e3f25cd22a4083f2177
---

# Kurulum

VOOL 0.7.7-beta'nın Apple Silicon Mac'ler, Intel Mac'ler ve Linux x86_64 için uygulama indirmeleri
[v0.7.7-beta sürümünde](https://github.com/Parad0x-Labs/vool/releases/tag/v0.7.7-beta) yer alır.
macOS, Linux veya Windows'ta önyükleme betiğiyle kaynak koddan da kurabilirsiniz.
İmzalama ve güncelleme ayrıntıları için [Sürüm durumu](../trust/release-status.md) sayfasına bakın.

## Gereksinimler

| | En az | Rahat |
| --- | ---: | ---: |
| Bellek (RAM) | 8 GB | 16 GB veya daha fazla |
| Disk | Uygulama için 2 GB | Yerel modellerle 20 GB |
| macOS | 14 Sonoma, Apple Silicon veya Intel ([sınırlar](#intel-macler)) | 14 Sonoma veya daha yenisi, Apple Silicon |
| Windows | 10 (64 bit) | 11 |
| Linux | Ubuntu 22.04, x86_64 | Ubuntu 24.04 |

Yerel modeller, uygulamanın kendisinden çok daha fazla bellek gerektirir. Boyut önerileri
[Yerel model çalıştırma](../guides/local-models.md) sayfasındadır.

## Uygulamayı indirin

| Bilgisayarınız | İndirme |
| --- | --- |
| Apple Silicon'lı Mac (M1 ve sonrası) | [`VOOL-0.7.7-beta-macos-arm64.dmg`](https://github.com/Parad0x-Labs/vool/releases/download/v0.7.7-beta/VOOL-0.7.7-beta-macos-arm64.dmg) |
| Intel işlemcili Mac | [`VOOL-0.7.7-beta-macos-x86_64.dmg`](https://github.com/Parad0x-Labs/vool/releases/download/v0.7.7-beta/VOOL-0.7.7-beta-macos-x86_64.dmg) |
| Linux x86_64 (Ubuntu 22.04 / 24.04) | [`vool-0.7.7-beta-linux-x86_64.tar.gz`](https://github.com/Parad0x-Labs/vool/releases/download/v0.7.7-beta/vool-0.7.7-beta-linux-x86_64.tar.gz) |

Mac'inizin işlemcisini öğrenmek için Apple menüsünü açın ve **Bu Mac Hakkında**'yı seçin. "Apple M…"
Apple Silicon, "Intel" ise Intel anlamına gelir. Apple Silicon uygulaması Intel Mac'te çalışmaz ve
açtığınızda bunu söyler.

Açmadan önce indirilen dosyanın SHA-256 değerini kontrol edin (bkz. [Bir indirmeyi doğrulama](#bir-indirmeyi-doğrulama)).

### Mac'te ilk açılış

Beta uygulaması henüz Apple tarafından onaylanmadığı için macOS, uygulamayı ilk açtığınızda onay ister.
Bu beklenen bir durumdur ve yalnızca bir kez olur.

1. DMG'yi açın ve **VOOL**'u **Uygulamalar**'a sürükleyin.
2. VOOL'a çift tıklayın. macOS, uygulamada kötü amaçlı yazılım olup olmadığını denetleyemediğini söyler.
   Mesajı kapatın.
3. **Sistem Ayarları → Gizlilik ve Güvenlik**'i açın. VOOL ile ilgili mesaja kadar aşağı kaydırın
   ve **Yine de Aç**'ı tıklayın. Onaylamak için parolanızı girin.

macOS 14 Sonoma'da bunun yerine Uygulamalar klasöründe Kontrol tuşuna basarak VOOL'u tıklayabilir, **Aç**'ı seçebilir
ve ardından yeniden **Aç**'ı tıklayabilirsiniz.

### Linux

Sürümden tar arşivini ve onun `.sha256` dosyasını indirin, arşivi kontrol edin, ardından birlikte gelen
yükleyiciyi çalıştırın. sudo veya sistem Python'u gerekmez.

```bash
sha256sum -c vool-0.7.7-beta-linux-x86_64.tar.gz.sha256
tar -xzf vool-0.7.7-beta-linux-x86_64.tar.gz
bash vool-0.7.7-beta-linux-x86_64/install.sh
~/.local/bin/vool
```

VOOL `~/.local/share/vool` içine kurulur, `vool` komutunu `~/.local/bin` içine koyar ve uygulama
menüsüne bir giriş ekler. Masaüstü uygulaması sohbeti tarayıcınızda açar; tarayıcıyı kapatmak
VOOL'u durdurmaz. VOOL'u kaldırmak için `bash ~/.local/share/vool/current/uninstall.sh` komutunu çalıştırın.
Sohbetleriniz ve ayarlarınız profilinizde kalır.

0.7.5'ten itibaren, **Kendi başına çevrimiçi olma** açıkken Linux indirmesi VOOL'un içinden güncellenir. Yeni sürüm eski sürümün yanına
kurulur. Düzgün başlamazsa VOOL kendiliğinden önceki sürüme geri döner.

### Kurulumdan sonra

Yerel bir modelin yanıt verebilmesi için önce **Ayarlar → Modeller ve sağlayıcılar → Yerel modeller**'i açın ve o model için bir kez **Certify**'a basın (daha önce bir denetim kaydedildiyse düğmede **Re-run certification** yazar). Bu, makinenizde bir denetim çalıştırır; ekran kartı olmayan bir bilgisayarda ilk denetim birkaç dakika sürebilir.

0.7.5'ten itibaren VOOL kendini günceller: "Kendi başına çevrimiçi olma" açıksa bir bildirim gösterir ve yeni sürümü tek dokunuşla kurar. Bir Mac'te 0.7.4 veya 0.7.3'ten güncelleme: "Kendi başına çevrimiçi olma"yı açın (Ayarlar > Gizlilik ve İzinler) ve bir kez "Güncellemeleri kontrol et"e basın. Linux'ta 0.7.4 veya daha eskisinden ya da Mac'te 0.7.2 ve daha eskisinden: 0.7.7'yi indirin ve mevcut sürümünüzün üzerine kurun. Sohbetleriniz ve ayarlarınız
uygulamanın dışında, profilinizde durur. Kripto özellikleri varsayılan olarak kapalıdır.

## Kaynak koddan kurulum

Platformunuz için önyükleme betiğini çalıştırın:

```bash
curl -fsSLo bootstrap_vool.sh https://raw.githubusercontent.com/Parad0x-Labs/vool/v0.7.7-beta/installer/bootstrap_vool.sh
bash bootstrap_vool.sh --ref v0.7.7-beta
```

```powershell
Invoke-WebRequest https://raw.githubusercontent.com/Parad0x-Labs/vool/main/installer/bootstrap_vool.ps1 -OutFile bootstrap_vool.ps1
powershell -ExecutionPolicy Bypass -File .\bootstrap_vool.ps1
```

macOS / Linux komutları hem önyükleme betiğini hem de kaynak kod indirmesini `v0.7.7-beta` sürümüne sabitler. Başka bir sürüm seçmek için hem betik URL'sindeki etiketi hem de `--ref` değerini değiştirin. Önyükleme betiği bir dal adını veya sürüm etiketini kabul eder, commit hash'ini kabul etmez.

Kaynak koddan kurulum için Python 3.11 veya daha yenisi gerekir. Bulunamazsa yükleyici bir tane indirip
kurar. Uygulama indirmeleri kendi Python'larını içerir.

Yükleyici Python ortamını hazırlar, donanımınızı kontrol eder, yerel modeli
kurar ve yerel hizmetleri başlatır. İlk model indirmesi için zaman ve disk alanı ayırın.
Kurulumdan sonra yerel model çıkarımı çevrimdışı çalışır; web araçları ve bulut modelleri için
bağlantı gerekir.

## Intel Mac'ler

VOOL, macOS 14 veya daha yenisinin yüklü olduğu Intel Mac'lerde (örneğin 2020 model 13 inç MacBook Pro)
Intel indirmesiyle ya da kaynak koddan çalışır. Kaynak kod yükleyicisi Intel CPU'yu algılar ve
tüm bağımlılıkların Intel derlemelerini kurar. İki sınır Intel'e özgüdür:

* **Yerel model eğitimi yok.** PyTorch, 2.3'ten beri Intel macOS için derleme yayımlamadı; bu yüzden
  yükleyici onu atlar ve LoRA eğitimi kullanılamadığını bildirir. Sohbet, hafıza,
  araçlar ve bulut modelleri bundan etkilenmez.
* **Yerel modeller yalnızca CPU'da çalışır.** Yerel model çalıştırıcılarının Intel grafikleri için GPU hızlandırması
  yoktur; bu yüzden yerel yanıtlar Apple Silicon'a göre daha yavaştır. 8 GB'lık bir Mac'te sohbet için
  bir bulut modeli kullanın; 16 GB'ta yükleyicinin varsayılan yerel modeli (bir Qwen modeli) çalışır ama yavaştır.

Intel Mac'lerde yerel modeller işlemcide çalışır ve yavaştır. Hafızaya dayanan uzun bir yanıt zaman aşımına uğrayabilir, bu yüzden şimdilik orada bir bulut modeli kullanın. Yerel model sertifikasyonu daha yavaş işlemcileri hesaba katar.

Intel Mac'lerin kendi indirmesi vardır: `VOOL-0.7.7-beta-macos-x86_64.dmg`. Apple Silicon indirmesi
Intel'de çalışmaz ve açılışta bunu söyler.

## Bir indirmeyi doğrulama

Sürümdeki her indirmenin yanında bir SHA-256 sağlama toplamı bulunur. Dosyayı açmadan önce
doğrulayın:

```bash
shasum -a 256 <downloaded-artifact>    # macOS
sha256sum <downloaded-artifact>         # Linux
```

```powershell
Get-FileHash -Algorithm SHA256 <downloaded-artifact>
```

Sonucu, o sürümle birlikte yayımlanan sağlama toplamıyla karşılaştırın. Farklıysa
dosyayı silin ve yeniden indirin.

## Sonraki adım

[İlk çalıştırma](first-run.md) ile devam edin.

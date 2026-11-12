---
description: VOOL'un nasıl yapıldığı: tek sayfada mimari, geliştirici kurulumu ve nasıl katkıda bulunulacağı.
translation: machine
source_sha256: 7cb91029e16c6510821e1ca26b509da8cdfb63113111619d3a31e364a2f088d3
---

# Mimari ve katkıda bulunma

## Tek sayfada mimari

VOOL, sunulan bir çekirdeğe sahip, önce yerel çalışan bir masaüstü çalışma zamanıdır:

```text
┌──────────────────────────────────────────────┐
│ Native window / browser console              │
│  (served pages; the console you use)         │
├──────────────────────────────────────────────┤
│ Served runtime (local HTTP API)              │
│  routing → tool execution → answers          │
├───────────────┬──────────────┬───────────────┤
│ Local models  │ Cloud lanes  │ Wallet (opt)  │
│ (Ollama etc.) │ (your keys)  │ pilot lanes   │
├───────────────┴──────────────┴───────────────┤
│ Records: receipts, ledgers, fault registry   │
│ Local storage (SQLite under your profile)    │
└──────────────────────────────────────────────┘
```

Kodun uyguladığı ilkeler:

* **Varsayılan olarak yerel; bulut sizin anahtarınızla.** Bulut şeritleri yalnızca sizin kimlik bilgileriniz ve sizin üst sınırlarınızla var olur.
* **Her karar için tek yetki.** İzinler, harcama ve ağ çıkışının her birinin tek bir sahip sözleşmesi vardır; hatalar, sahibi oldukları sınırda sınıflandırılır.
* **Neyin başarısız olduğu söylenir.** Bilinmeyen sonuçların türü bilinmiyor olarak belirlenir; retler neyin olmadığını söyler; sabitlenmiş bir model asla sessizce değiştirilmez.
* **Her şey kaydedilir.** Araç çağrıları, onaylar, ödemeler ve güncellemeler profilinizin altına makbuz yazar.

## Geliştirici kurulumu

```bash
git clone https://github.com/Parad0x-Labs/vool.git
cd vool
python3 -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
pytest tests -q            # the suite
```

Platform notları, araç zincirinin tam düzeni ve sürüm kapıları deponun mühendislik belgelerinde bulunur (depo kökündeki `docs/`; derin iç yapılar bu herkese açık belge sitesinin parçası değildir).

## Belgeler: tek kanonik kaynak

Bu belgeler **tek** bir kaynaktan oluşturulur: bu deponun `docs/` klasörü (GitBook düzeni, depo kökünde `.gitbook.yaml`). Herkese açık belge sitesi ondan oluşturulur. Belgeler üzerinde çalışmak için:

```bash
python3 tools/docs/build_docs.py --src . --out docs-build --base /docs
python3 tools/docs/check_docs_build.py docs-build /docs
python3 -m http.server -d docs-build 8000   # local preview
```

Hata başvurusu (`ERROR_BOOK.md`) ve yerelleştirme devir manifesti **çalışma zamanının kendi kayıt defterlerinden oluşturulur**; onları yeniden oluşturun, asla elle düzenlemeyin:

```bash
python -m tools.generate_error_book
python -m tools.generate_localization_handoff
```

Odaklı bir test, oluşturulan dosyalar kayıt defterlerinden saparsa derlemeyi başarısız kılar.

## Katkıda bulunma

* Küçük, incelenmiş değişiklikler; belgelerdeki her iddia, yayımlanan haliyle kodla eşleşmelidir.
* Kararlı kodları ve tanımlayıcıları koruyun; bunlar derlemelerden uzun yaşar.
* Bir yetkiyi asla sessizce genişletmeyin: yeni bir izin, harcama yolu ya da çıkış şeridi bir yama değil, bir tasarım değişikliğidir.
* Beta sınırlamaları gizlenmez, belgelenir; belgelenmemiş bir sınırlama bulursanız bu, belgelerde bir hatadır.

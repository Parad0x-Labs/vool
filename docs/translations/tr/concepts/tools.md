---
description: Araçlar, VOOL'un yapabileceği eylemlerdir ve her biri kaydedilir.
translation: machine
source_sha256: 53722a7f30f4446b6d029465a402a823f60496a66008b5df150d201c1963b152
---

# Araçlar

Araç, çalışma zamanının sizin adınıza yapabileceği bir eylemdir: bir dosyayı okumak, bir komut çalıştırmak, bir klasörde arama yapmak.

## Tasarımı gereği incelenebilir

Her araç çağrısı neyin istendiğini, neyin çalıştığını ve ne döndüğünü kaydeder. Bir çalıştırma, yeniden çalıştırılmadan sonradan okunabilir.

## İzin

Bir aracın hemen mi çalışacağı yoksa önce mi soracağı, seçtiğiniz moda ve eyleme bağlıdır. Yeni bir sohbet, her değişikliğin önce sorduğu Manuel modda başlar. Otomatik modda dosya oluşturma ve düzenleme, komutlar ve ağ eylemleri sormadan çalışır; silme, üzerine yazma, kurulum, Git commit ve push işlemleri, dağıtım, ileti gönderme, ödemeler ve ayarları değiştirme ise yine sorar. Her modda dosya araçları yalnızca [çalışma alanı](workspaces.md) içinde çalışır, arama ve getirme araçları hiçbir şey yazmadan herkese açık kaynakları okur ve saklanan gizli bilgiler asla okunmaz. [İzinler](permissions.md) beş modun hepsini ve her onay düğmesinin neye izin verdiğini anlatır.

```text
tool: read_file
path: src/main.py
result: 412 lines
```

Ayrıca bkz. [Makbuzlar](receipts.md).

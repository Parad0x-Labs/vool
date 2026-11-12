---
description: VOOL'un oturumlar arasında bir şeyleri nasıl hatırladığı ve bu hafızanın nerede durduğu.
translation: machine
source_sha256: 65af63e755076fe33675d7a5d6afa7f0c5664b77f716ffc0dc1f0f358e67f667
---

# Hafıza

Hafıza, VOOL'un bilgileri bir oturumdan diğerine taşıma biçimidir. Makinenizde, VOOL'un veri klasöründe, okunabilir biçimde dosyalar olarak saklanır.

## Ne kadar iyi hatırlıyor

LoCoMo uzun konuşma karşılaştırma testinden alınan ve daha önce hiç kullanılmamış 150 soruda, VOOL 0.7 ile gelen hafıza 123 soruyu doğru yanıtladı (%82,0). Aynı sorularla yapılan karşılaştırmalı turda, üçü için de aynı model yanıt verirken VOOL 124 (%82,7), OpenClaw 107 (%71,3) ve Hermes 94 (%62,7) doğru yanıt aldı; VOOL bunu yaparken soru başına 3,5 ila 5,4 kat daha az token gönderdi. Her çalıştırma yayımlanmıştır: [hafıza karşılaştırmaları](https://github.com/Parad0x-Labs/vool/tree/main/docs/benchmarks).

## Özellikler

* **Diskte, açıkta.** Hafıza, okuyabileceğiniz dosyalardan oluşur. `MEMORY.md` onun okunabilir yüzüdür; arkasındaki bilgiler, yanındaki bir girdiler dosyasında tutulur.
* **Yerel.** Hafıza bir sunucuyla eşitlenmez.
* **Her VOOL ana klasörü için tek depo.** Hafıza çalışma alanlarına göre bölünmez; tüm kurulum için tutulur, bu yüzden proje başına bir yalıtım sınırı değildir.

## Yönetme

`MEMORY.md` dosyasına kendiniz eklediğiniz notlar korunur. VOOL'un kaydettiği bilgiler dosya silinerek değil, VOOL'un unutma işlemiyle ortadan kaldırılır: unutma, girdi deposunu ve ayna kopyayı tek adımda yeniden yazar ve metnin gittiğini doğrulamak için ikisini de yeniden okur. Yalnızca `MEMORY.md` dosyasını silmek bilgileri ortadan kaldırmaz: girdiler dosyası bilgileri hâlâ tutar ve ayna kopya ondan yeniden oluşturulur.

Bulut modunda ilgili hafıza, diğer her bağlamla aynı koşullarda, sağlayıcıya gönderilen bağlamın bir parçası olabilir. Bkz. [Yerel ve bulut](local-and-cloud.md).

Bir sohbette unutmak, sözcükleri o sohbetin çalışma zamanı veritabanındaki çalıştırma geçmişinden de siler: tur kayıtları, kontrol noktaları, denemeler, görev planları, kesinleşmiş yanıtlar (yanıt silme işleminin taradığı kopyalarla birlikte), aday bilgiler, bağlam raporları, bildirimler, sohbetin ürettiği araştırma çıktıları ve bunlardan alıntı yapan sabitlenmiş iletiler. Başka kayıtların bağlı olduğu kayıtlar yerinde kalır; ancak unutulan sözcükler ve yanlarında saklanan özet değerleri boşaltılır. Geri kalanlar silinir. Unutma geldiğinde hâlâ çalışmakta olan bir tur sözcükleri geri yazamaz: olayları boşaltılır ve tur bittiğinde kayıtları yeniden silinir. Ardından veritabanının write-ahead günlüğü ana dosyaya aktarılır; böylece eski kopyalar `-wal` dosyasında kalmaz. Başka bir sohbetin kayıtlarına dokunulmaz. Henüz gönderilmemiş, sırada bekleyen bir ileti korunur ve unutma işlemi o zaman tam olarak uygulanamadığını söyler. Hiçbir silme adımının tanımadığı bir tablonun adı yanıtta verilir ve yanıt, yeniden denemenin onu temizlemeyeceğini söyler.

Sözcükler büyük/küçük harf ayrımı yapılmadan karşılaştırılır ve başka bir yazı sisteminde yazılmış bir rakam o rakam sayılır (tam genişlikli "１２", 12 demektir). Unutulan bir sayı yalnızca kendi başına bir sayı olarak durduğu yerde eşleşir: onunla bitişik yazılan harf ve rakamlar başka bir rakam içeremez ve sayı `.`, `,`, `:`, `/`, `-` ya da `_` üzerinden başka bir rakama devam edemez. "12" sayısını unutmak "kod 12", "12kg" ve "B12" ifadelerini siler, ama "oda 312", "3.12", "12.5", "12:30" ya da "2026-10-12" ifadelerini silmez; "200" sayısını unutmak da "1,200" ifadesini silmez. Çalıştırma geçmişinde, çalışma zamanının saydığı ya da süresini ölçtüğü yalın bir sayı (örneğin bir `elapsed_ms` ya da `step_count` değeri) eşleşmez.

Hafıza okuyucuları ayrıca kalıcı bir unutma otoritesini de denetler. Yeni bir hafıza ana klasörü, yazma kabul etmeden önce bu otoriteyi oluşturur. Daha eski bir anlamsal depo, yalnızca daha önce kaydedilmiş bir silme yoksa yerinde yükseltilir; saklanan hafıza kayıtları kullanılabilir kalır. Var olan ama okunamayan bir otorite, kalıcı bir tanığı bulunan eksik bir otorite ya da kaydedilmiş bir unutma bariyeri, okuyucuların etkilenen hafızayı vermemesine yol açar. Otorite dosyalarını silmek, unutulan içeriği geri getirmenin bir yolu değildir.

Bekleyen bir silme günlüğü, kurtarma için belirtecini saklar. Kalıcı ve kapsamı belirli iptal oluştuktan sonra tamamlama adımı, işlemin kimliğini ve durumunu korurken günlüğün artık gereksiz olan belirtecini temizler. İptal otoritesi kalır.

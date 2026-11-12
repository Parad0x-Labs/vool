---
description: Makbuz, bir çalıştırmanın gerçekte ne yaptığının kaydıdır.
translation: machine
source_sha256: caffc8be79cb1017a81f74baa2451e076db2e6660302b57db4b9d3ec92dab8b8
---

# Makbuzlar

Makbuz, bir çalıştırmanın kalıcı kaydıdır: hangi araçların hangi girdilerle çağrıldığı ve her birinin ne döndürdüğü.

## Neden varlar

Bir ajanın kendi işi hakkındaki özeti bir iddiadır. Makbuz, bu iddiayı karşısında kontrol edebileceğiniz kayıttır.

## İçinde ne var

| Alan | Anlamı |
| --- | --- |
| `tool` | Hangi eylemin çalıştığı |
| `input` | Ona ne verildiği |
| `result` | Ne döndürdüğü |
| `time` | Ne zaman çalıştığı |

Makbuzlar proje klasörlerinizde değil, bu bilgisayarda VOOL'un "runtime home" klasöründe tutulur (bkz. [Yapılandırma](../reference/configuration.md)). Bir makbuz makinenizden yalnızca sizin önizleyip göndermeyi seçtiğiniz bir hata raporunun içinde çıkar (bkz. [Hata bildirimi ve gizlilik](../trust/bug-reporting.md)).

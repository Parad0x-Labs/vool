---
description: VOOL'u kendi kod tabanınızda bir yapay zekâ kodlama asistanı olarak kullanma: çalışma alanını ayarlama, kod hakkında soru sorma ve düzenlemeleri inceleme.
translation: machine
source_sha256: 1d1c4e7c4222eed4dc6caae24dca70729496ead8b88b1fc45a81a61ed548241a
---

# VOOL ile kod yazma

## Çalışma alanını depoya ayarlayın

[Çalışma alanını](../concepts/workspaces.md) projenin kök klasörüne yöneltin. VOOL tüm ağacı yüklemek yerine ihtiyaç duyduğu dosyaları okur.

## Kodun terimleriyle sorun

Bir dosyanın ya da bir sembolün adını vermek, çalışma zamanına bir başlangıç noktası sağlar:

> `src/auth/` içinde oturum zaman aşımının nerede ayarlandığını bul ve süresi dolduğunda ne olduğunu açıkla.

## Uygulamadan önce inceleyin

Manuel modda her düzenleme önce sorar. Otomatik modda günlük düzenlemeler ve komutlar otomatik olarak çalışabilir ve etkinlik kaydında görünür; silmeler ve üzerine yazmalar ise yine sorar. Büyük bir değişiklikten önce commit yapın; böylece fark kolay okunur ve kolay geri alınır.

## Yerel ya da bulut

Kodlama her iki modda da çalışır. 7B'lik yerel bir kodlama modeli günlük düzenlemelerin çoğunu halleder; daha büyük bulut modelleri daha uzun akıl yürütmeyi üstlenir. Model göstergesi, göndermeden önce hangisinin etkin olduğunu söyler: yerel bir model için `LOCAL`, bulut modeli için `CLOUD` ve VOOL seçtiğinde varsayılan olarak `AUTO` (`VOOL Auto`, `Local first`).

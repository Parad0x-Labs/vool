---
description: Kişiler: her düzenlemeyi koruyan değişiklik incelemesiyle, kaydedilmiş kişiler ve uç noktalar.
translation: machine
source_sha256: 8be5d771229a4d6c4b439af014f0407649ed694fd26f9fd880009d25f5b8a347
---

# Kişiler

Kişi listesi, VOOL'un sizden bir adresi yeniden yazmanızı istemek yerine kullanabileceği yerel, yapılandırılmış bir adres defteridir (kişiler, uç noktaları ve ödeme takma adları). Adres defteri ödemeleri ve iletileri beslediği için üzerindeki düzenlemeler **korumalıdır**: incelenir, onaylanır ve kaydedilir.

## Korumalı düzenlemeler

Kayıtlı bir kişiyi değiştirmek iki adımlı bir akıştır:

1. **İnceleme** — değişiklik hazırlanır ve gösterilir. Girdi siz açtıktan sonra değiştiyse (başka bir pencerede, başka bir cihazda), değişiklik *"changed after this was reviewed; review it again. Nothing changed."* iletisiyle reddedilir.
2. **Onay** — değişiklik yalnızca inceleme ekranında PIN'iniz ya da parolanızla kaydedilir.

Her ret, **hiçbir şeyin kaydedilmediğini ya da değiştirilmediğini** açıkça söyler; çünkü gerçekten hiçbir şey kaydedilmemiştir. PIN'iniz kabul edildiyse ama kaydetmenin kendisi başarısız olduysa ileti tam olarak bunu söyler: PIN kabul edildi, onayın süresi dolmadan yeniden kaydedin.

## Korumalar

* Bir kişi için aynı anda tek bir değişiklik; bekleyen değişikliklerin sayısı sınırlıdır.
* Bir kişiyi silmek geçmişi sessizce yeniden yazmaz: silinmiş bir kişiye yapılan başvurular *"That contact was deleted"* olarak bildirilir; asla sessizce başka birine çözümlenmez.
* İçe aktarmalar asla kısmen uygulanmaz: kaynak okunamadıysa hiçbir şey içe aktarılmamıştır.

## Neden bu tasarım

Yanlış yazılmış bir ödeme adresi, geri alınamayan paradır. İnceleme adımı; hiçbir model turunun, hiçbir toplu düzenlemenin ve hiçbir eski pencerenin, bir insan ortaya çıkan girdinin tam halini onaylamadan "Alice" adının kimi gösterdiğini değiştirememesi için vardır.

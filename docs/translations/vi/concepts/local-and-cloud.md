---
description: Dữ liệu nào rời khỏi máy tính của bạn khi bạn dùng VOOL, và vào lúc nào. Sự khác biệt giữa chạy AI cục bộ và dùng mô hình đám mây.
translation: machine
source_sha256: 2c2b9e26e99579c00a032dfeac95fd216fe9229497c965c1bc08aface7a6c071
---

# Cục bộ và đám mây

VOOL có thể trả lời theo hai cách: trên máy tính này, hoặc qua một nhà cung cấp đám mây. Phần đầu khung trò chuyện cho biết yêu cầu tiếp theo chạy theo cách nào — ví dụ **Tự động** cùng **Ưu tiên cục bộ** khi VOOL Auto trả lời từ máy tính này.

## Cục bộ

Mô hình và các tệp của bạn ở lại trên máy tính này. Mô hình chạy trên một môi trường chạy cục bộ, tệp được đọc
từ ổ đĩa, và không có yêu cầu nào được gửi tới nhà cung cấp. Thông tin trực tiếp như thời tiết hay giá cả chỉ được lấy từ các dịch vụ web công khai khi "Tra cứu web" đang bật (bản cài mới thì nó tắt), và không bao giờ trong "Chỉ cục bộ".

## Đám mây (tên nhà cung cấp)

Ngữ cảnh đã chọn được gửi thẳng tới nhà cung cấp bạn đã chọn, từ máy của bạn,
bằng khóa của bạn.

"Ngữ cảnh đã chọn" là những gì ứng dụng tập hợp cho yêu cầu đó: lời nhắc của bạn, cùng với
các tệp và các lượt trước mà yêu cầu cần đến.

{% hint style="danger" %}
Chế độ đám mây gửi dữ liệu ra khỏi máy của bạn. Đó chính là mục đích của chế độ đám mây. Điều VOOL
khẳng định là về *đường đi* — yêu cầu đi từ máy tính của bạn tới nhà cung cấp của bạn — chứ không phải
là không có gì được truyền đi.
{% endhint %}

## Những gì nằm ở giữa

Không có gì do Parad0x Labs vận hành. Không có proxy, cổng trung gian (gateway) hay máy chủ chuyển tiếp
nào cho lưu lượng mô hình. Ứng dụng trên máy của bạn dùng khóa của bạn để giao tiếp trực tiếp với nhà cung cấp.

Chi tiết đầy đủ: [Xử lý dữ liệu](../trust/data-handling.md).

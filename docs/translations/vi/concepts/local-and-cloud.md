---
description: Dữ liệu nào rời khỏi máy tính của bạn khi bạn dùng VOOL, và vào lúc nào. Sự khác biệt giữa chạy AI cục bộ và dùng mô hình đám mây.
translation: machine
source_sha256: 3217e13bf6d684853b92d19097627d07e64bdd17745560b1204871cb0b7b0753
---

# Cục bộ và đám mây

VOOL có hai trạng thái. Giao diện luôn cho biết trạng thái nào đang bật.

## Cục bộ

Mọi thứ ở lại trên máy tính này. Mô hình chạy trên một môi trường chạy cục bộ, tệp được đọc
từ ổ đĩa, và không có yêu cầu nào được gửi tới nhà cung cấp.

## Đám mây (tên nhà cung cấp)

Ngữ cảnh đã chọn được gửi thẳng tới nhà cung cấp bạn đã chọn, từ máy của bạn,
bằng khóa của bạn.

"Ngữ cảnh đã chọn" là những gì ứng dụng tập hợp cho yêu cầu đó: lời nhắc của bạn, cùng với
các tệp và các lượt trước mà yêu cầu cần đến. Nó được hiển thị trước khi gửi.

{% hint style="danger" %}
Chế độ đám mây gửi dữ liệu ra khỏi máy của bạn. Đó chính là mục đích của chế độ đám mây. Điều VOOL
khẳng định là về *đường đi* — yêu cầu đi từ máy tính của bạn tới nhà cung cấp của bạn — chứ không phải
là không có gì được truyền đi.
{% endhint %}

## Những gì nằm ở giữa

Không có gì do Parad0x Labs vận hành. Không có proxy, cổng trung gian (gateway) hay máy chủ chuyển tiếp
nào cho lưu lượng mô hình. Ứng dụng trên máy của bạn dùng khóa của bạn để giao tiếp trực tiếp với nhà cung cấp.

Chi tiết đầy đủ: [Xử lý dữ liệu](../trust/data-handling.md).

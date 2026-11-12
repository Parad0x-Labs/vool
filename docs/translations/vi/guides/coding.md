---
description: Cách dùng VOOL làm trợ lý lập trình AI trên mã nguồn của chính bạn — đặt không gian làm việc, hỏi về mã và xem lại các chỉnh sửa.
translation: machine
source_sha256: 112dfd866a96b0f7fd22f6f57f7f6480d7ac293466fdf0260a74f3bc0b870036
---

# Lập trình với VOOL

## Đặt không gian làm việc là kho mã

Trỏ [không gian làm việc](../concepts/workspaces.md) vào thư mục gốc của dự án. VOOL đọc những tệp nó
cần thay vì nạp toàn bộ cây thư mục.

## Hỏi theo ngôn ngữ của mã

Nêu tên một tệp hoặc một ký hiệu (symbol) sẽ cho runtime một điểm bắt đầu:

> Tìm nơi đặt thời gian chờ của phiên trong `src/auth/` và giải thích điều gì xảy ra khi nó
> hết hạn.

## Xem lại trước khi áp dụng

Ở chế độ Thủ công, mọi chỉnh sửa đều hỏi trước. Ở chế độ Tự động, các chỉnh sửa và lệnh thường ngày có
thể chạy tự động và hiện trong bản ghi hoạt động, còn việc xóa và ghi đè vẫn hỏi. Hãy commit trước một
thay đổi lớn để phần khác biệt (diff) dễ đọc và dễ hoàn tác.

## Cục bộ hay đám mây

Lập trình hoạt động ở cả hai chế độ. Một mô hình lập trình cục bộ 7B xử lý được phần lớn các chỉnh sửa
hằng ngày; các mô hình đám mây lớn hơn xử lý suy luận dài hơn. Chỉ báo chế độ cho bạn biết chế độ nào
đang bật trước khi bạn gửi.

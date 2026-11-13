---
description: VOOL gửi chính xác những dữ liệu nào, khi nào và cho ai. Những gì ở lại trên máy tính của bạn và những gì đến được nhà cung cấp AI của bạn.
translation: machine
source_sha256: 4fd0aaa4d2480ae80b94aa7132c46c8263113450e4b02c5747388791e7bc8710
---

# Xử lý dữ liệu

## Hai trạng thái

**Cục bộ** — mô hình và các tệp của bạn ở lại trên máy tính này. Thông tin trực tiếp như thời tiết hay giá cả chỉ được lấy từ các dịch vụ web công khai khi "Tra cứu web" đang bật (bản cài mới thì nó tắt), và không bao giờ trong "Chỉ cục bộ".

**Đám mây** — ngữ cảnh đã chọn được gửi thẳng tới nhà cung cấp đã chọn.

Phần đầu khung trò chuyện cho biết yêu cầu tiếp theo chạy theo cách nào — ví dụ **Tự động** cùng **Ưu tiên cục bộ** khi VOOL Auto trả lời từ máy tính này.

## Những gì VOOL không làm

* Nó không chuyển lời nhắc của bạn qua hạ tầng của Parad0x Labs.
* Nó không tải không gian làm việc của bạn lên mạng.
* Nó không gửi khóa của bạn tới bất kỳ đâu ngoài nhà cung cấp sở hữu khóa đó.

## Riêng với OpenRouter

Tài khoản OpenRouter của bạn vẫn là của bạn. VOOL gửi yêu cầu thẳng từ máy tính này
bằng khóa của bạn. Parad0x không làm trung gian cho lời nhắc của bạn và không trả tiền cho mức sử dụng của bạn. Mức sử dụng
VOOL thực tế có thể xuất hiện trong thống kê tổng hợp công khai của VOOL trên OpenRouter, nhưng từng lời nhắc
và danh tính riêng lẻ không được công bố.

## Đo từ xa

VOOL không gửi dữ liệu đo từ xa (telemetry) của sản phẩm. Báo cáo lỗi chỉ rời khỏi máy của bạn khi bạn xem trước
và chọn gửi nó; xem [Báo cáo lỗi và quyền riêng tư](bug-reporting.md).

## Áp dụng điều khoản của nhà cung cấp

Ở chế độ đám mây, dữ liệu của bạn đến nhà cung cấp theo điều khoản lưu giữ và huấn luyện
**của họ**, không phải của VOOL. Hãy đọc điều khoản của nhà cung cấp mà bạn chọn.

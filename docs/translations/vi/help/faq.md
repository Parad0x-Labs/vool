---
description: Những câu hỏi thường gặp về VOOL — nó có hoạt động ngoại tuyến không, khóa API của bạn được lưu ở đâu, nó có miễn phí không, và nó hỗ trợ những hệ thống nào.
translation: machine
source_sha256: d98db6dcf1791e9de7aed2dbb7204ab774afd9ea47d883bc0211640a8895413e
---

# Câu hỏi thường gặp

## VOOL có hoạt động ngoại tuyến không?

Có, với một mô hình cục bộ. Mô hình đám mây theo định nghĩa cần kết nối mạng.

## Tôi có cần khóa đám mây không?

Không. Chỉ cần một mô hình cục bộ là đủ để dùng ứng dụng.

## Khóa của tôi được lưu ở đâu?

Trong một tệp đã mã hóa trong runtime home của VOOL trên máy tính của bạn, chỉ tài khoản người dùng của bạn
đọc được. Các khóa mà VOOL đã giữ trong Chuỗi khóa (Keychain) của macOS trên máy Mac vẫn ở đó. Khóa ở lại
trên máy của bạn. Xem [Mô hình bảo mật](../trust/security.md#khóa).

## Parad0x có thấy lời nhắc của tôi không?

Không ở chế độ cục bộ, và cũng không ở chế độ đám mây — các yêu cầu đám mây đi từ máy của bạn tới nhà cung
cấp của bạn. Xem [Xử lý dữ liệu](../trust/data-handling.md).

## Vì sao hệ thống cảnh báo khi tôi mở ứng dụng?

Các ứng dụng macOS được ký ad-hoc nhưng chưa được Apple công chứng, nên macOS yêu cầu bạn xác nhận lần mở
đầu tiên. Các bước có trong [Cài đặt VOOL](../getting-started/install.md#mở-lần-đầu-trên-mac). Xem
[Trạng thái phát hành](../trust/release-status.md).

## Tôi có thể dùng mô hình của riêng mình không?

Có. Bất kỳ môi trường chạy nào cung cấp một điểm cuối cục bộ tương thích OpenAI. Xem
[Mô hình được hỗ trợ](../reference/models.md).

## Nó có miễn phí không?

Ứng dụng miễn phí trong giai đoạn beta. Mức sử dụng mô hình đám mây do nhà cung cấp của bạn tính phí, vào
tài khoản của bạn.

---
description: Giới hạn chi tiêu, chốt chặn giá, và điều gì xảy ra khi một lệnh gọi trả phí bị gián đoạn.
translation: machine
source_sha256: a4a5246059942702330cbfc18e9872dc5b0f46192fa5b852bfc9060ca987eae6
---

# Giới hạn chi tiêu và chốt chặn giá

VOOL không bao giờ tiêu tiền thay bạn khi không có một mức trần rõ ràng do bạn kiểm soát. Trang này mô
tả các lớp bảo vệ lệnh gọi mô hình trả phí và tài nguyên trả phí, và điều gì xảy ra khi một lệnh gọi trả
phí bị gián đoạn giữa chừng.

## Hai mức giá, một quy tắc

* **Giá danh mục** — mức giá mà nhà cung cấp hiện đang công bố cho một mô hình. VOOL tìm ra nó; đó là
  thông tin, không phải sự cho phép.
* **Giá bạn đã chấp nhận** — mức trần bạn đã phê duyệt cho nhà cung cấp và mô hình đó trong Cài đặt. Một
  mức giá được phát hiện cao hơn mức trần của bạn sẽ bị từ chối **trước** khi liên hệ với nhà cung cấp.
  Lời từ chối nói đúng điều đó: mô hình không chạy, không có gì được gửi, không có khoản phí nào bị tính.

## Giới hạn chi tiêu

Với các lệnh gọi trả phí, VOOL giữ chỗ tiền theo các giới hạn trước mỗi lệnh gọi:

| Giới hạn | Ý nghĩa |
| --- | --- |
| Mỗi lệnh gọi | Mức trần chi phí dự kiến của một lệnh gọi. |
| Mỗi tác vụ | Số tiền tối đa một tác vụ có thể chi qua các lệnh gọi của nó. |
| Hằng ngày / hằng tháng | Mức trần cho toàn bộ kỳ đối với mức sử dụng trả phí. |

Một lệnh gọi sẽ vượt quá một giới hạn thì bị từ chối trước khi được gửi. Lời từ chối nêu tên giới hạn đã
từ chối nó (`per_task_spend_cap_exceeded` và các mã cùng loại) và cách diễn đạt được cố ý đặt ở thời điểm
trước khi gửi: *"running it would exceed your spend cap"* (chạy nó sẽ vượt quá giới hạn chi tiêu của bạn)
— không bao giờ là một khẳng định rằng nhà cung cấp đã được liên hệ.

## Mô hình đã ghim không bao giờ bị thay thế một cách lặng lẽ

Nếu bạn chọn rõ một mô hình trả phí và một chốt chặn từ chối nó, VOOL **không** lặng lẽ trả lời bằng một
mô hình rẻ hơn. Lượt đó thất bại, kèm tên mô hình bạn đã chọn, tên chốt chặn thực sự đã chặn, và câu
*"không có mô hình nào khác được dùng thay thế."* Hãy tự chọn một mô hình khác, hoặc chủ động nâng giới
hạn.

## Đã trả tiền, nhưng câu trả lời không bao giờ đến

Nếu một lệnh gọi trả phí đã bị tính tiền nhưng câu trả lời không đến, kết quả là **không xác định** — VOOL
sẽ không giả vờ rằng nó đã thất bại hay thành công:

* Không có câu trả lời nào được bịa ra để thay thế, và không có khoản hoàn tiền nào được mặc định.
* Khoản thanh toán được lưu trong hồ sơ ở trạng thái chưa được giải quyết.
* Cài đặt liệt kê nó dưới **Paid calls waiting for an answer**, nơi **Resume** yêu cầu cùng câu trả lời
  đó một lần, **mà không phải trả tiền lần nữa**.

Đây là lý do việc thử lại một cách mù quáng không bao giờ được gợi ý trong những trường hợp này: chỉ gửi
lại đơn thuần có thể trả tiền hai lần. Resume là cách duy nhất được hỗ trợ.

## Thanh toán tự động bằng ví (x402)

Các trang web yêu cầu thanh toán x402 bằng USDC trên Base hoặc Base Sepolia có thể được thanh toán từ ví
Crypto Pilot của bạn, với một lần phê duyệt bằng PIN trong phạm vi các giới hạn này; chưa có khoản thanh
toán mainnet thật nào được thực hiện theo cách này. Tùy chọn trả tiền theo từng lệnh gọi trong chợ mô hình
ở Cài đặt thanh toán từ ví Solana Mainnet, một lần phê duyệt cho mỗi lệnh gọi trong phạm vi các giới hạn
này, và chưa được thử nghiệm trên một chuỗi thật.
Các tài nguyên thanh toán bằng ví (xem [Ví](../guides/wallet.md)) có giới hạn thanh toán tự động riêng.
Một tài nguyên đòi nhiều hơn giới hạn đó đơn giản là không được thanh toán; bạn có thể chủ động nâng giới
hạn hoặc thanh toán cho tài nguyên đó qua một đề xuất tường minh có sự phê duyệt của bạn.

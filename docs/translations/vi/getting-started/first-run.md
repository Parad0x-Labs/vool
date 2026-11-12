---
description: Những gì trợ lý AI VOOL hỏi bạn vào lần đầu mở, và những gì nó không làm.
translation: machine
source_sha256: cedf20410d47a2d277e5522bb3e55169854a91a267d71193a762e072e7f8e47e
---

# Lần chạy đầu tiên

Lần khởi chạy đầu tiên hỏi bốn câu ngắn. Bạn có thể bỏ qua bất kỳ câu nào và thay đổi mọi câu trả lời
sau này. Trong lúc bạn trả lời, không có gì được gửi đi đâu cả, ngoại trừ việc kiểm tra khóa đám mây với
nhà cung cấp của khóa đó nếu bạn thêm một khóa.

## Điều gì diễn ra

1. **Nó nên suy nghĩ ở đâu?** Trên máy tính này với một mô hình cục bộ, hoặc trực tuyến với khóa của một
   nhà cung cấp mà bạn trả tiền. Xem [Kết nối mô hình](connect-a-model.md).
2. **Chọn một thư mục.** Thư mục dự án mà VOOL làm việc trong đó. Xem
   [Không gian làm việc](../concepts/workspaces.md).
3. **Nó được tự làm gì?** Mức độ thường xuyên VOOL dừng lại hỏi bạn trước khi thay đổi bất cứ thứ gì. Xem
   [Công cụ](../concepts/tools.md).
4. **Nó nên gọi bạn là gì?** Không bắt buộc. Một cái tên hoặc biệt danh là đủ, và thông tin đó ở lại trên
   máy tính này.

Sau đó, chỉ báo chế độ hiển thị Cục bộ, Tự động hoặc tên nhà cung cấp đám mây, và phản ánh mô hình
đang được chọn.

**Chứng nhận mô hình cục bộ một lần.** Trước khi một mô hình cục bộ có thể trả lời, hãy mở
**Cài đặt → Mô hình và nhà cung cấp → Mô hình cục bộ** và nhấn **Certify** cho mô hình đó. Thao tác này chạy một
bước kiểm tra ngắn trên máy của bạn.

## Những gì VOOL không làm ở lần chạy đầu tiên

* Nó không tạo tài khoản.
* Nó không bắt buộc khóa đám mây. Chỉ cần một mô hình cục bộ là đủ để dùng ứng dụng.
* Nó không lập chỉ mục ổ đĩa của bạn. Nó chỉ đọc tệp khi một yêu cầu cần đến; xem
  [Không gian làm việc](../concepts/workspaces.md) để biết những thư mục nó có thể truy cập.

{% hint style="info" %}
Bạn có thể chọn thư mục dự án khác bất cứ lúc nào bằng **+ Dự án** ở thanh bên trò chuyện.
{% endhint %}

## Tiếp theo

Tiếp tục với [Kết nối mô hình](connect-a-model.md).

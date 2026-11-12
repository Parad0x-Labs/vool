---
description: Cách VOOL tự cập nhật, điều gì xảy ra với dữ liệu của bạn, và khả năng tương thích giữa các phiên bản.
translation: machine
source_sha256: 3b6a950556f4669f116dfa3a911ffb5e07ad8bcfa04415d68cf5a3d1adf0fe65
---

# Cập nhật và chuyển đổi dữ liệu

## Cách cập nhật hoạt động

Từ 0.7.5 trở đi, VOOL tự cập nhật: khi "Tự lên mạng" đang bật, nó hiện thông báo và cài phiên bản mới chỉ với một lần nhấn. Cập nhật từ 0.7.4 hoặc 0.7.3 trên Mac: bật "Tự lên mạng" (Cài đặt > Riêng tư và Quyền) rồi nhấn Kiểm tra bản cập nhật một lần. Từ 0.7.4 trở về trước trên Linux, hoặc 0.7.2 trở về trước trên Mac: tải 0.7.7 và cài đè lên phiên bản hiện tại. Các cuộc trò chuyện và thiết lập của bạn nằm trong hồ sơ của bạn, bên ngoài ứng dụng.

Khi "Tự lên mạng" đang bật, VOOL kiểm tra phiên bản mới khi khởi động và mỗi ngày một lần, và hiện thông
báo mà không cần nhấn Kiểm tra. Khi nó tắt, VOOL không tải gì về và thanh tiêu đề hiển thị
**Đã tắt kiểm tra cập nhật**.

Một gói cập nhật đã ký chạy qua một quy trình đã được xác minh, có tự động khôi phục (rollback):

1. **Tải xuống** — được xác minh dựa trên một bản kê khai đã ký.
2. **Cài đặt song song** — phiên bản mới được cài cạnh phiên bản cũ.
3. **Chuyển đổi dữ liệu** — dữ liệu của bạn được chuyển sang phiên bản kho mới. Nếu việc chuyển đổi thất
   bại, dữ liệu của bạn được khôi phục về phiên bản trước và ứng dụng khởi động lại trên phiên bản đó:
   *"Dữ liệu của bạn đã được khôi phục về phiên bản trước."*
4. **Kiểm tra tình trạng** — phiên bản mới phải chứng minh nó có thể phục vụ trước khi phiên bản cũ được
   cho nghỉ.
5. **Hoán đổi** — diễn ra nguyên tử; phiên bản trước được giữ lại làm đích để khôi phục.

Trên Linux, bản cài từ gói tar cập nhật theo cùng cách. Phiên bản mới được cài cạnh phiên bản đang chạy,
và `current` chuyển sang nó trong một bước. Nếu phiên bản mới không hoạt động tốt, VOOL tự chuyển về
phiên bản trước. **Roll back** thủ công có sẵn trên Mac và cho bản cài từ mã nguồn, nhưng chưa có cho bản
cài từ gói tar trên Linux.

Nếu bất kỳ giai đoạn nào thất bại, ứng dụng được giữ nguyên như cũ — thông báo lỗi nêu tên giai đoạn đó
(`verification_failed`, `insufficient_disk_space`, …) và mỗi giai đoạn có câu hướng dẫn khắc phục riêng.

## Tương thích dữ liệu

* Hồ sơ, cuộc trò chuyện, biên nhận và cài đặt của bạn nằm bên ngoài gói ứng dụng, nên việc thay thế ứng
  dụng không bao giờ thay thế dữ liệu của bạn.
* Kho cục bộ từ chối mở khi **hạ cấp**: một cơ sở dữ liệu do một phiên bản VOOL mới hơn ghi sẽ không bị
  một phiên bản cũ hơn lặng lẽ đọc — nó báo như vậy và từ chối, vì đoán một bố cục kho không xác định sẽ
  làm hỏng kho. Hãy cài lại phiên bản mới hơn để đọc dữ liệu mới.
* Các tệp được cài bởi bản cập nhật và chính trạng thái cập nhật được ghi lại trong hồ sơ của bạn kèm biên
  nhận, nên câu hỏi "phiên bản nào đã động đến dữ liệu của tôi" có thể được trả lời sau đó.

## Kênh beta

Dòng phát hành hiện tại là bản **beta** (xem [Trạng thái phát hành](../trust/release-status.md)). Các bản
cập nhật được ký bằng khóa nhà phát hành đã ghim và được kiểm tra trước khi cài. Bản thân ứng dụng macOS
chỉ được ký ad-hoc và chưa được Apple công chứng, nên macOS có thể yêu cầu bạn xác nhận lần khởi chạy đầu
tiên của một phiên bản mới.

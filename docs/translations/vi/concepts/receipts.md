---
description: Biên nhận là bản ghi về những gì một lần chạy thực sự đã làm.
translation: machine
source_sha256: caffc8be79cb1017a81f74baa2451e076db2e6660302b57db4b9d3ec92dab8b8
---

# Biên nhận

Biên nhận là bản ghi lâu bền của một lần chạy: những công cụ nào đã được gọi, với đầu vào nào, và mỗi
công cụ đã trả về gì.

## Vì sao có biên nhận

Bản tóm tắt của một tác tử về công việc của chính nó là một lời khẳng định. Biên nhận là bản ghi mà bạn có
thể dùng để đối chiếu lời khẳng định đó.

## Bên trong một biên nhận có gì

| Trường | Ý nghĩa |
| --- | --- |
| `tool` | Hành động nào đã chạy |
| `input` | Nó được đưa gì |
| `result` | Nó trả về gì |
| `time` | Nó chạy khi nào |

Biên nhận được giữ trong runtime home của VOOL trên máy tính này (xem
[Cấu hình](../reference/configuration.md)), không phải trong các thư mục dự án của bạn. Một biên nhận chỉ
rời khỏi máy của bạn khi nằm trong một báo cáo lỗi mà bạn xem trước và chọn gửi đi (xem
[Báo cáo lỗi và quyền riêng tư](../trust/bug-reporting.md)).

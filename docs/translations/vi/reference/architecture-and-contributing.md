---
description: VOOL được xây dựng như thế nào — kiến trúc trong một trang, thiết lập cho nhà phát triển, và cách đóng góp.
translation: machine
source_sha256: 7cb91029e16c6510821e1ca26b509da8cdfb63113111619d3a31e364a2f088d3
---

# Kiến trúc và đóng góp

## Kiến trúc trong một trang

VOOL là một runtime trên máy tính, ưu tiên cục bộ, với một phần lõi được phục vụ (served core):

```text
┌──────────────────────────────────────────────┐
│ Native window / browser console              │
│  (served pages; the console you use)         │
├──────────────────────────────────────────────┤
│ Served runtime (local HTTP API)              │
│  routing → tool execution → answers          │
├───────────────┬──────────────┬───────────────┤
│ Local models  │ Cloud lanes  │ Wallet (opt)  │
│ (Ollama etc.) │ (your keys)  │ pilot lanes   │
├───────────────┴──────────────┴───────────────┤
│ Records: receipts, ledgers, fault registry   │
│ Local storage (SQLite under your profile)    │
└──────────────────────────────────────────────┘
```

Các nguyên tắc mà mã nguồn áp đặt:

* **Mặc định là cục bộ; đám mây bằng khóa của bạn.** Các làn đám mây chỉ tồn tại khi có thông tin đăng
  nhập và mức trần của bạn.
* **Mỗi quyết định một thẩm quyền.** Quyền, chi tiêu và lưu lượng mạng đi ra ngoài, mỗi thứ có một hợp
  đồng sở hữu duy nhất; các sự cố được phân loại tại ranh giới sở hữu chúng.
* **Nói rõ điều gì đã thất bại.** Kết quả không xác định được gán kiểu là không xác định; lời từ chối nói
  rõ điều gì đã không xảy ra; một mô hình đã ghim không bao giờ bị lặng lẽ thay thế.
* **Mọi thứ đều được ghi lại.** Các lệnh gọi công cụ, phê duyệt, thanh toán và cập nhật đều ghi biên nhận
  trong hồ sơ của bạn.

## Thiết lập cho nhà phát triển

```bash
git clone https://github.com/Parad0x-Labs/vool.git
cd vool
python3 -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
pytest tests -q            # the suite
```

Ghi chú về nền tảng, bố cục đầy đủ của bộ công cụ và các cổng kiểm duyệt phát hành nằm trong tài liệu kỹ
thuật của kho mã (`docs/` ở thư mục gốc của kho; phần chi tiết nội bộ sâu không thuộc trang tài liệu công
khai này).

## Tài liệu: một nguồn chuẩn duy nhất

Tài liệu này được tạo ra từ **một** nguồn: thư mục `docs/` của kho mã này (bố cục GitBook, `.gitbook.yaml`
ở thư mục gốc của kho). Trang tài liệu công khai được dựng từ đó. Để làm việc với tài liệu:

```bash
python3 tools/docs/build_docs.py --src . --out docs-build --base /docs
python3 tools/docs/check_docs_build.py docs-build /docs
python3 -m http.server -d docs-build 8000   # local preview
```

Phần tham khảo lỗi (`ERROR_BOOK.md`) và bản kê khai bàn giao bản địa hóa được
**tạo ra từ chính các sổ đăng ký của runtime** — hãy tạo lại chúng, đừng bao giờ sửa tay:

```bash
python -m tools.generate_error_book
python -m tools.generate_localization_handoff
```

Một bài kiểm thử chuyên biệt sẽ khiến bản dựng thất bại nếu các tệp được tạo ra lệch khỏi sổ đăng ký của
chúng.

## Đóng góp

* Các thay đổi nhỏ, có xem xét; mọi khẳng định trong tài liệu phải khớp với mã nguồn như đã phát hành.
* Giữ nguyên các mã và định danh ổn định — chúng tồn tại lâu hơn các bản dựng.
* Đừng bao giờ âm thầm nới rộng một thẩm quyền: một quyền mới, một đường chi tiêu mới hay một làn đi ra
  mạng mới là một thay đổi thiết kế, không phải một bản vá.
* Các hạn chế của bản beta được ghi trong tài liệu, không bị che giấu — nếu bạn tìm thấy một hạn chế chưa
  được ghi lại, đó là một lỗi trong tài liệu.

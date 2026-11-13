---
translation: machine
source_sha256: 5a872f9502c66828af4dfb1e7e687226facc2aaad8cb6544054c90e1fafe8d7d
---

# Sổ tay lỗi VOOL

<!-- GENERATED FILE — do not hand-edit. Regenerate with `python -m tools.generate_error_book`. -->

<!-- GENERATED FILE: the app's own texts for each code, and the words in tools/docs/error_book_words.json. Regenerate with tools/docs/translate_error_book.py, never by hand. -->

Mọi lỗi mà VOOL có thể hiển thị cho bạn, được tạo từ chính danh mục lỗi của ứng dụng: các mã, thông báo và bước tiếp theo bên dưới đúng y như những gì ứng dụng hiển thị.

- **Lược đồ:** `vool.fault.v1`
- **Phiên bản danh mục:** `1`
- **Mã băm danh mục:** `2d2c27206b99b26ca524806ec42ac41eac2f0d28a110a74f6ac5ef42006f313d`
- **Số lỗi:** 59

Mỗi mã đều cố định và không bao giờ được dùng lại với một nghĩa khác. Mỗi mã có một mục riêng ở bên dưới, với tiêu đề chính là mã đó, nên khi tìm trên trang này đúng mã như bạn nhận được, bạn sẽ thấy mục của nó, và liên kết đến mục đó là địa chỉ của trang này, theo sau là `#` và mã với dấu gạch dưới viết thành dấu gạch ngang. Mỗi mục cho biết chuyện gì đã xảy ra, điều gì có thể đã xảy ra rồi, cần làm gì tiếp theo và thử lại có giúp được không. Nếu một mã bạn nhận được không có trong sổ tay này, mã đó không đến từ phiên bản VOOL này: hãy kiểm tra phiên bản nào đã hiển thị nó, hoặc báo cáo kèm theo mã đúng như đã viết.

## Bảo mật

| Mã | Tiêu đề | Mức nghiêm trọng | Điều gì có thể đã xảy ra rồi |
|---|---|---|---|
| [`confinement_refusal`](#confinement-refusal) | Thay đổi ngoài phạm vi được phép | cao | Chưa gửi, ký hay thay đổi gì |
| [`credential_failure`](#credential-failure) | Không thể đọc thông tin xác thực đã lưu | cao | Chưa gửi, ký hay thay đổi gì |
| [`wallet_backup_unavailable`](#wallet-backup-unavailable) | Bản dự phòng đã được hiển thị một lần | cao | Chưa gửi, ký hay thay đổi gì |
| [`wallet_caller_refused`](#wallet-caller-refused) | Không bắt đầu từ cửa sổ của chính VOOL | cao | Chưa gửi, ký hay thay đổi gì |
| [`wallet_card_data_refused`](#wallet-card-data-refused) | Dữ liệu thẻ bị từ chối | cao | Chưa gửi, ký hay thay đổi gì |
| [`wallet_chain_identity_mismatch`](#wallet-chain-identity-mismatch) | Danh tính điểm cuối chuỗi chưa được chứng minh | cao | Một yêu cầu đã đến dịch vụ bên ngoài; không có khoản thanh toán nào được chuyển |
| [`wallet_environment_inactive`](#wallet-environment-inactive) | Sai môi trường mạng | trung bình | Chưa gửi, ký hay thay đổi gì |
| [`wallet_export_pin_not_accepted`](#wallet-export-pin-not-accepted) | Việc xuất chỉ mở bằng xác thực thiết bị | cao | Chưa gửi, ký hay thay đổi gì |
| [`wallet_export_refused`](#wallet-export-refused) | Cách xuất cũ đã bị ngừng | cao | Chưa gửi, ký hay thay đổi gì |
| [`wallet_export_unavailable`](#wallet-export-unavailable) | Không thể xuất ví này | cao | Chưa gửi, ký hay thay đổi gì |
| [`wallet_legacy_surface_retired`](#wallet-legacy-surface-retired) | Con đường tiền cũ đã bị ngừng | cao | Chưa gửi, ký hay thay đổi gì |
| [`wallet_network_disabled`](#wallet-network-disabled) | Mạng chưa được tuyên bố cho ví này | cao | Chưa gửi, ký hay thay đổi gì |
| [`wallet_outbound_refused`](#wallet-outbound-refused) | Yêu cầu ngoài các điểm cuối đã phê duyệt | cao | Chưa gửi, ký hay thay đổi gì |
| [`wallet_quote_mismatch`](#wallet-quote-mismatch) | Phê duyệt không còn khớp với bản xem trước | cao | Chưa gửi, ký hay thay đổi gì |
| [`wallet_recovery_refused`](#wallet-recovery-refused) | Bằng chứng khôi phục không khớp ví này | cao | Chưa gửi, ký hay thay đổi gì |
| [`wallet_signature_invalid`](#wallet-signature-invalid) | Chữ ký không khớp khóa của ví | cao | Một yêu cầu đã đến dịch vụ bên ngoài; không có khoản thanh toán nào được chuyển |
| [`wallet_unlock_throttled`](#wallet-unlock-throttled) | Ví bị khóa sau các lần mở khóa thất bại | cao | Chưa gửi, ký hay thay đổi gì |

## Tính toàn vẹn

| Mã | Tiêu đề | Mức nghiêm trọng | Điều gì có thể đã xảy ra rồi |
|---|---|---|---|
| [`evidence_corruption`](#evidence-corruption) | Bản ghi của câu trả lời không qua được kiểm tra nhất quán | cao | Đã có một bản ghi hoặc thay đổi cục bộ |
| [`integrity_verification_failure`](#integrity-verification-failure) | Bản ghi không qua được kiểm tra chống giả mạo | nghiêm trọng | Đã có một bản ghi hoặc thay đổi cục bộ |
| [`unsupported_claim`](#unsupported-claim) | Các phát biểu không được ủng hộ đã bị giữ lại | trung bình | Đã có một bản ghi hoặc thay đổi cục bộ |
| [`wallet_duplicate_payment`](#wallet-duplicate-payment) | Khoản thanh toán đã được đề xuất hoặc gửi | trung bình | Đã có một bản ghi hoặc thay đổi cục bộ |

## Chính sách

| Mã | Tiêu đề | Mức nghiêm trọng | Điều gì có thể đã xảy ra rồi |
|---|---|---|---|
| [`evm_pocket_custody_unavailable`](#evm-pocket-custody-unavailable) | Túi cũ chỉ dùng cho Solana | thấp | Chưa gửi, ký hay thay đổi gì |
| [`permission_denied`](#permission-denied) | Bị từ chối quyền | trung bình | Chưa gửi, ký hay thay đổi gì |
| [`wallet_acknowledgement_required`](#wallet-acknowledgement-required) | Cần xác nhận | thấp | Chưa gửi, ký hay thay đổi gì |
| [`wallet_amount_invalid`](#wallet-amount-invalid) | Số tiền không chính xác với đồng này | thấp | Chưa gửi, ký hay thay đổi gì |
| [`wallet_approval_rejected`](#wallet-approval-rejected) | Phê duyệt không được chấp nhận | trung bình | Chưa gửi, ký hay thay đổi gì |
| [`wallet_confirmation_required`](#wallet-confirmation-required) | Cần xác nhận đã gõ | thấp | Chưa gửi, ký hay thay đổi gì |
| [`wallet_credential_mismatch`](#wallet-credential-mismatch) | Các mục nhập không khớp | thấp | Chưa gửi, ký hay thay đổi gì |
| [`wallet_dependency_unavailable`](#wallet-dependency-unavailable) | Chưa cài thư viện EVM | trung bình | Chưa gửi, ký hay thay đổi gì |
| [`wallet_device_auth_denied`](#wallet-device-auth-denied) | Mở khóa bằng thiết bị bị từ chối | thấp | Chưa gửi, ký hay thay đổi gì |
| [`wallet_disabled`](#wallet-disabled) | Ví đang tắt | thấp | Chưa gửi, ký hay thay đổi gì |
| [`wallet_insufficient_funds`](#wallet-insufficient-funds) | Số dư không đủ cho số tiền và phí | thấp | Một yêu cầu đã đến dịch vụ bên ngoài; không có khoản thanh toán nào được chuyển |
| [`wallet_limit_exceeded`](#wallet-limit-exceeded) | Vượt giới hạn chi tiêu | trung bình | Chưa gửi, ký hay thay đổi gì |
| [`wallet_not_found`](#wallet-not-found) | Không nhận diện được ví, đề xuất hoặc đích đến | thấp | Chưa gửi, ký hay thay đổi gì |
| [`wallet_password_invalid`](#wallet-password-invalid) | Mật khẩu ví không hợp lệ | thấp | Chưa gửi, ký hay thay đổi gì |
| [`wallet_paykit_unavailable`](#wallet-paykit-unavailable) | Chưa cài phần bổ sung pay cho Solana | trung bình | Chưa gửi, ký hay thay đổi gì |
| [`wallet_payment_channel_off`](#wallet-payment-channel-off) | Kênh thanh toán đang tắt | thấp | Chưa gửi, ký hay thay đổi gì |
| [`wallet_pin_invalid`](#wallet-pin-invalid) | PIN không hợp lệ | thấp | Chưa gửi, ký hay thay đổi gì |
| [`wallet_quote_expired`](#wallet-quote-expired) | Bản xem trước đã hết hạn hoặc bị thay thế | thấp | Chưa gửi, ký hay thay đổi gì |
| [`wallet_recipient_refused`](#wallet-recipient-refused) | Người nhận không thể an toàn nhận khoản này | trung bình | Chưa gửi, ký hay thay đổi gì |
| [`wallet_request_ambiguous`](#wallet-request-ambiguous) | Có nhiều hơn một tài khoản hoặc mạng khớp | thấp | Chưa gửi, ký hay thay đổi gì |
| [`wallet_send_needs_vool_wallet`](#wallet-send-needs-vool-wallet) | Cần VOOL Wallet để gửi | thấp | Chưa gửi, ký hay thay đổi gì |
| [`wallet_setup_state_invalid`](#wallet-setup-state-invalid) | Bước thiết lập hiện không áp dụng | thấp | Chưa gửi, ký hay thay đổi gì |
| [`wallet_signing_unavailable`](#wallet-signing-unavailable) | Ví không thể ký từ đây | trung bình | Chưa gửi, ký hay thay đổi gì |
| [`wallet_storage_class_refused`](#wallet-storage-class-refused) | Lưu khóa chỉ trong bộ nhớ | trung bình | Chưa gửi, ký hay thay đổi gì |
| [`wallet_x402_cap_exceeded`](#wallet-x402-cap-exceeded) | Tài nguyên trả phí vượt mức trần thanh toán tự động | trung bình | Chưa gửi, ký hay thay đổi gì |
| [`wallet_x402_sdk_unavailable`](#wallet-x402-sdk-unavailable) | Chưa cài phần bổ sung x402 | trung bình | Chưa gửi, ký hay thay đổi gì |
| [`x402_scheme_unavailable`](#x402-scheme-unavailable) | Lược đồ thanh toán không được hỗ trợ | thấp | Chưa gửi, ký hay thay đổi gì |

## Tính khả dụng

| Mã | Tiêu đề | Mức nghiêm trọng | Điều gì có thể đã xảy ra rồi |
|---|---|---|---|
| [`provider_exhausted`](#provider-exhausted) | Đã đạt giới hạn của nhà cung cấp | trung bình | Một yêu cầu đã đến dịch vụ bên ngoài; không có khoản thanh toán nào được chuyển |
| [`provider_unavailable`](#provider-unavailable) | Không thể kết nối nhà cung cấp | trung bình | Chưa gửi, ký hay thay đổi gì |
| [`timeout`](#timeout) | Hết thời gian chờ | trung bình | Không rõ kết quả — hãy kiểm tra trạng thái trước khi thử lại |
| [`tool_unavailable`](#tool-unavailable) | Công cụ không khả dụng | thấp | Chưa gửi, ký hay thay đổi gì |
| [`wallet_broadcast_failed`](#wallet-broadcast-failed) | Không thể phát đi khoản thanh toán | trung bình | Không rõ kết quả — hãy kiểm tra trạng thái trước khi thử lại |
| [`wallet_device_auth_unavailable`](#wallet-device-auth-unavailable) | Không dùng được mở khóa bằng thiết bị | trung bình | Chưa gửi, ký hay thay đổi gì |
| [`wallet_quote_unavailable`](#wallet-quote-unavailable) | Không rõ số dư hoặc phí | trung bình | Một yêu cầu đã đến dịch vụ bên ngoài; không có khoản thanh toán nào được chuyển |
| [`wallet_security_key_needed`](#wallet-security-key-needed) | Cần khóa bảo mật | thấp | Chưa gửi, ký hay thay đổi gì |
| [`wallet_simulation_failed`](#wallet-simulation-failed) | Khoản thanh toán không qua mô phỏng | trung bình | Một yêu cầu đã đến dịch vụ bên ngoài; không có khoản thanh toán nào được chuyển |

## Hủy

| Mã | Tiêu đề | Mức nghiêm trọng | Điều gì có thể đã xảy ra rồi |
|---|---|---|---|
| [`cancelled`](#cancelled) | Đã hủy | thông tin | Không rõ kết quả — hãy kiểm tra trạng thái trước khi thử lại |

## Nội bộ

| Mã | Tiêu đề | Mức nghiêm trọng | Điều gì có thể đã xảy ra rồi |
|---|---|---|---|
| [`unknown`](#unknown) | Lỗi không mong đợi | trung bình | Không rõ kết quả — hãy kiểm tra trạng thái trước khi thử lại |

## Tra cứu theo mã

Mọi mã, theo thứ tự bảng chữ cái. Tiêu đề của mục là neo cố định của mã.

<a name="cancelled"></a>

### cancelled

**Đã hủy**

- **Chuyện gì đã xảy ra:** Việc này đã bị hủy nên chưa hoàn thành.
- **Điều gì có thể đã xảy ra rồi:** Không rõ kết quả — hãy kiểm tra trạng thái trước khi thử lại
- **Cần làm gì tiếp theo:** Không cần làm gì; bắt đầu lại nếu bạn muốn nó được hoàn thành.
- **Thử lại:** Đừng gửi lại cùng một yêu cầu mà không thay đổi gì.
- **Mức nghiêm trọng:** thông tin · **Danh mục:** hủy

<a name="confinement-refusal"></a>

### confinement_refusal

**Thay đổi ngoài phạm vi được phép**

- **Chuyện gì đã xảy ra:** Một thay đổi tệp ngoài phạm vi được phép đã bị chặn. Không có gì ngoài phạm vi bị ảnh hưởng.
- **Điều gì có thể đã xảy ra rồi:** Chưa gửi, ký hay thay đổi gì
- **Cần làm gì tiếp theo:** Nếu muốn thay đổi này, hãy yêu cầu rõ ràng để nó nằm trong phạm vi được phép.
- **Thử lại:** Đừng gửi lại cùng một yêu cầu mà không thay đổi gì.
- **Mức nghiêm trọng:** cao · **Danh mục:** bảo mật · **Liên quan đến bảo mật:** có

<a name="credential-failure"></a>

### credential_failure

**Không thể đọc thông tin xác thực đã lưu**

- **Chuyện gì đã xảy ra:** Không thể đọc một thông tin xác thực đã lưu. Nhập lại trong cài đặt để tiếp tục.
- **Điều gì có thể đã xảy ra rồi:** Chưa gửi, ký hay thay đổi gì
- **Cần làm gì tiếp theo:** Mục trong két không giải mã được; lưu lại thông tin xác thực hoặc kiểm tra khóa két.
- **Thử lại:** Đừng gửi lại cùng một yêu cầu mà không thay đổi gì.
- **Mức nghiêm trọng:** cao · **Danh mục:** bảo mật · **Liên quan đến bảo mật:** có

<a name="evidence-corruption"></a>

### evidence_corruption

**Bản ghi của câu trả lời không qua được kiểm tra nhất quán**

- **Chuyện gì đã xảy ra:** Các bản ghi hậu thuẫn cho câu trả lời này không qua được kiểm tra nhất quán, hãy coi câu trả lời là chưa xác minh.
- **Điều gì có thể đã xảy ra rồi:** Đã có một bản ghi hoặc thay đổi cục bộ
- **Cần làm gì tiếp theo:** Kiểm tra sổ thực thi của lượt này với luồng sự kiện của thời gian chạy.
- **Thử lại:** Đừng gửi lại cùng một yêu cầu mà không thay đổi gì.
- **Mức nghiêm trọng:** cao · **Danh mục:** tính toàn vẹn · **Liên quan đến bảo mật:** có

<a name="evm-pocket-custody-unavailable"></a>

### evm_pocket_custody_unavailable

**Túi cũ chỉ dùng cho Solana**

- **Chuyện gì đã xảy ra:** Túi cũ, tức ví dựa trên cụm khôi phục, chỉ giữ khóa Solana, nên không thể tạo hay khôi phục trên mạng EVM. Chưa tạo gì. Với mạng EVM, hãy tạo Ví VOOL trên mạng đó trong Cài đặt, Tiền điện tử, thêm tài khoản ở chế độ chỉ-xem, hoặc kết nối ví EVM của riêng bạn.
- **Điều gì có thể đã xảy ra rồi:** Chưa gửi, ký hay thay đổi gì
- **Cần làm gì tiếp theo:** Túi cũ niêm phong một seed Solana duy nhất từ cụm khôi phục của nó. Trên mạng EVM, hãy dùng ví Crypto Pilot ("Create wallet" của mạng đó trong Cài đặt, Tiền điện tử, Mạng và ví), một tài khoản chỉ-xem, hoặc một ví EVM bên ngoài (EIP-1193) tự giữ khóa của nó.
- **Thử lại:** Đừng gửi lại cùng một yêu cầu mà không thay đổi gì.
- **Mức nghiêm trọng:** thấp · **Danh mục:** chính sách

<a name="integrity-verification-failure"></a>

### integrity_verification_failure

**Bản ghi không qua được kiểm tra chống giả mạo**

- **Chuyện gì đã xảy ra:** Một bản ghi không qua được kiểm tra chống giả mạo. Không có gì được chấp nhận lặng lẽ.
- **Điều gì có thể đã xảy ra rồi:** Đã có một bản ghi hoặc thay đổi cục bộ
- **Cần làm gì tiếp theo:** Coi chuỗi biên lai bị ảnh hưởng là đã xâm phạm; kiểm tra trước khi tin các mục của nó.
- **Thử lại:** Đừng gửi lại cùng một yêu cầu mà không thay đổi gì.
- **Mức nghiêm trọng:** nghiêm trọng · **Danh mục:** tính toàn vẹn · **Liên quan đến bảo mật:** có

<a name="permission-denied"></a>

### permission_denied

**Bị từ chối quyền**

- **Chuyện gì đã xảy ra:** Hành động đó không được phép, nên không có gì thay đổi.
- **Điều gì có thể đã xảy ra rồi:** Chưa gửi, ký hay thay đổi gì
- **Cần làm gì tiếp theo:** Mở Cài đặt, chọn Riêng tư và Quyền, rồi đặt "VOOL được tự làm gì" để thay đổi điều chi phối hành động này.
- **Thử lại:** Đừng gửi lại cùng một yêu cầu mà không thay đổi gì.
- **Mức nghiêm trọng:** trung bình · **Danh mục:** chính sách · **Liên quan đến bảo mật:** có

<a name="provider-exhausted"></a>

### provider_exhausted

**Đã đạt giới hạn của nhà cung cấp**

- **Chuyện gì đã xảy ra:** Đã đạt giới hạn sử dụng của nhà cung cấp mô hình. Bạn có thể thử lại sau.
- **Điều gì có thể đã xảy ra rồi:** Một yêu cầu đã đến dịch vụ bên ngoài; không có khoản thanh toán nào được chuyển
- **Cần làm gì tiếp theo:** Kiểm tra hạn mức, giới hạn tốc độ và thanh toán của nhà cung cấp đã cấu hình.
- **Thử lại:** Hãy thử lại sau: điều đã chặn việc này sẽ tự hết theo thời gian.
- **Mức nghiêm trọng:** trung bình · **Danh mục:** tính khả dụng

<a name="provider-unavailable"></a>

### provider_unavailable

**Không thể kết nối nhà cung cấp**

- **Chuyện gì đã xảy ra:** Không thể kết nối nhà cung cấp mô hình. Bạn có thể thử lại sau ít phút.
- **Điều gì có thể đã xảy ra rồi:** Chưa gửi, ký hay thay đổi gì
- **Cần làm gì tiếp theo:** Kiểm tra trạng thái nhà cung cấp, khả năng tiếp cận mạng và các điểm cuối đã cấu hình.
- **Thử lại:** Hãy thử lại sau: điều đã chặn việc này sẽ tự hết theo thời gian.
- **Mức nghiêm trọng:** trung bình · **Danh mục:** tính khả dụng

<a name="timeout"></a>

### timeout

**Hết thời gian chờ**

- **Chuyện gì đã xảy ra:** Việc này quá lâu và đã bị dừng. Thử lại có thể thành công.
- **Điều gì có thể đã xảy ra rồi:** Không rõ kết quả — hãy kiểm tra trạng thái trước khi thử lại
- **Cần làm gì tiếp theo:** Nếu hết giờ lặp lại, kiểm tra độ trễ mạng và độ phản hồi của nhà cung cấp.
- **Thử lại:** Thử lại ngay bây giờ có thể thành công.
- **Mức nghiêm trọng:** trung bình · **Danh mục:** tính khả dụng

<a name="tool-unavailable"></a>

### tool_unavailable

**Công cụ không khả dụng**

- **Chuyện gì đã xảy ra:** Công cụ đó hiện không khả dụng, nên phần này chưa làm. Cần bật nó trước khi thử lại.
- **Điều gì có thể đã xảy ra rồi:** Chưa gửi, ký hay thay đổi gì
- **Cần làm gì tiếp theo:** Mở Cài đặt, chọn Riêng tư và Quyền, rồi đặt "VOOL được tự làm gì", hoặc chọn cách khác.
- **Thử lại:** Hãy thử lại sau khi thực hiện thay đổi mà mục này nêu.
- **Mức nghiêm trọng:** thấp · **Danh mục:** tính khả dụng

<a name="unknown"></a>

### unknown

**Lỗi không mong đợi**

- **Chuyện gì đã xảy ra:** Đã có trục trặc trên máy này khi xử lý việc đó. Bạn có thể thử lại.
- **Điều gì có thể đã xảy ra rồi:** Không rõ kết quả — hãy kiểm tra trạng thái trước khi thử lại
- **Cần làm gì tiếp theo:** Xem chuỗi nguyên nhân đã chỉnh sửa của lỗi trong chẩn đoán; nguyên nhân không rõ không bao giờ được thử lại mù quáng.
- **Thử lại:** Đừng gửi lại cùng một yêu cầu mà không thay đổi gì.
- **Mức nghiêm trọng:** trung bình · **Danh mục:** nội bộ

<a name="unsupported-claim"></a>

### unsupported_claim

**Các phát biểu không được ủng hộ đã bị giữ lại**

- **Chuyện gì đã xảy ra:** Một hoặc nhiều phát biểu không được các nguồn truy xuất ủng hộ và đã bị giữ lại.
- **Điều gì có thể đã xảy ra rồi:** Đã có một bản ghi hoặc thay đổi cục bộ
- **Cần làm gì tiếp theo:** Không cần làm gì — đó là cổng kiểm chứng đang làm việc. Nếu thường xuyên, kiểm tra chất lượng tìm kiếm.
- **Thử lại:** Đừng gửi lại cùng một yêu cầu mà không thay đổi gì.
- **Mức nghiêm trọng:** trung bình · **Danh mục:** tính toàn vẹn

<a name="wallet-acknowledgement-required"></a>

### wallet_acknowledgement_required

**Cần xác nhận**

- **Chuyện gì đã xảy ra:** Bước này cần bạn xác nhận trước, nên chưa ký hay gửi gì. Hãy đọc điều ví nói về việc hành động này làm, xác nhận đúng nguyên văn, rồi thử lại.
- **Điều gì có thể đã xảy ra rồi:** Chưa gửi, ký hay thay đổi gì
- **Cần làm gì tiếp theo:** Khi một lần phê duyệt sẽ cấp quyền chi tiêu lâu dài, đổi quyền sở hữu hay chạy một bản nâng cấp, hoặc ví thấy có điểm không khớp hay không đọc được yêu cầu, ví sẽ yêu cầu bạn gõ đúng văn bản xác nhận trước; không gì chung chung thay thế được.
- **Thử lại:** Hãy thử lại sau khi thực hiện thay đổi mà mục này nêu.
- **Mức nghiêm trọng:** thấp · **Danh mục:** chính sách

<a name="wallet-amount-invalid"></a>

### wallet_amount_invalid

**Số tiền không chính xác với đồng này**

- **Chuyện gì đã xảy ra:** Số tiền đó không phải số chính xác đồng này có thể giữ, nên chưa đề xuất gì. Kiểm tra số tiền rồi thử lại.
- **Điều gì có thể đã xảy ra rồi:** Chưa gửi, ký hay thay đổi gì
- **Cần làm gì tiếp theo:** Gửi một số thập phân giản dị, không chính xác hơn mức đồng cho phép và trong hạn mức mỗi lần chuyển của chương trình thí điểm.
- **Thử lại:** Hãy thử lại sau khi thực hiện thay đổi mà mục này nêu.
- **Mức nghiêm trọng:** thấp · **Danh mục:** chính sách

<a name="wallet-approval-rejected"></a>

### wallet_approval_rejected

**Phê duyệt không được chấp nhận**

- **Chuyện gì đã xảy ra:** Phê duyệt cho khoản thanh toán này không được chấp nhận, nên chưa ký hay gửi gì.
- **Điều gì có thể đã xảy ra rồi:** Chưa gửi, ký hay thay đổi gì
- **Cần làm gì tiếp theo:** Phê duyệt đúng bộ tham số bằng PIN hoặc sinh trắc học của chủ sở hữu; phê duyệt không chuyển giữa các bộ tham số.
- **Thử lại:** Đừng gửi lại cùng một yêu cầu mà không thay đổi gì.
- **Mức nghiêm trọng:** trung bình · **Danh mục:** chính sách · **Liên quan đến bảo mật:** có

<a name="wallet-backup-unavailable"></a>

### wallet_backup_unavailable

**Bản dự phòng đã được hiển thị một lần**

- **Chuyện gì đã xảy ra:** Khóa dự phòng của ví này đã được hiển thị một lần nên không thể hiện lại. Nếu bạn chưa ghi lại, hãy hủy thiết lập và không gửi tiền vào địa chỉ này.
- **Điều gì có thể đã xảy ra rồi:** Chưa gửi, ký hay thay đổi gì
- **Cần làm gì tiếp theo:** Bản dự phòng Chương trình Tiền điện tử được tiết lộ đúng một lần khi thiết lập; không có lần thứ hai và không xuất khẩu.
- **Thử lại:** Đừng gửi lại cùng một yêu cầu mà không thay đổi gì.
- **Mức nghiêm trọng:** cao · **Danh mục:** bảo mật · **Liên quan đến bảo mật:** có

<a name="wallet-broadcast-failed"></a>

### wallet_broadcast_failed

**Không thể phát đi khoản thanh toán**

- **Chuyện gì đã xảy ra:** Không thể phát thanh khoản thanh toán. Kiểm tra trạng thái của nó trước khi thử lại.
- **Điều gì có thể đã xảy ra rồi:** Không rõ kết quả — hãy kiểm tra trạng thái trước khi thử lại
- **Cần làm gì tiếp theo:** Kiểm tra điểm cuối RPC của mạng và biên lai đề xuất; khoản chi chưa được ghi là đã gửi.
- **Thử lại:** Hãy thử lại sau: điều đã chặn việc này sẽ tự hết theo thời gian.
- **Mức nghiêm trọng:** trung bình · **Danh mục:** tính khả dụng

<a name="wallet-caller-refused"></a>

### wallet_caller_refused

**Không bắt đầu từ cửa sổ của chính VOOL**

- **Chuyện gì đã xảy ra:** Hành động ví này chỉ có thể bắt đầu từ cửa sổ của VOOL trên máy tính này, nên chưa thay đổi, ký hay gửi gì. Mở VOOL và thử lại ở đó.
- **Điều gì có thể đã xảy ra rồi:** Chưa gửi, ký hay thay đổi gì
- **Cần làm gì tiếp theo:** Điều khiển các cánh cửa ví tin cậy từ trang được phục vụ hoặc cửa sổ gốc; trang từ nguồn khác, khung hay plugin bị từ chối.
- **Thử lại:** Hãy thử lại sau khi thực hiện thay đổi mà mục này nêu.
- **Mức nghiêm trọng:** cao · **Danh mục:** bảo mật · **Liên quan đến bảo mật:** có

<a name="wallet-card-data-refused"></a>

### wallet_card_data_refused

**Dữ liệu thẻ bị từ chối**

- **Chuyện gì đã xảy ra:** Số thẻ và mã bảo mật không bao giờ lưu ở đây; chỉ chấp nhận token của nhà cung cấp. Chưa lưu gì.
- **Điều gì có thể đã xảy ra rồi:** Chưa gửi, ký hay thay đổi gì
- **Cần làm gì tiếp theo:** Token hóa thẻ với nhà cung cấp và đăng ký tham chiếu token.
- **Thử lại:** Đừng gửi lại cùng một yêu cầu mà không thay đổi gì.
- **Mức nghiêm trọng:** cao · **Danh mục:** bảo mật · **Liên quan đến bảo mật:** có

<a name="wallet-chain-identity-mismatch"></a>

### wallet_chain_identity_mismatch

**Danh tính điểm cuối chuỗi chưa được chứng minh**

- **Chuyện gì đã xảy ra:** Điểm cuối chuỗi không chứng minh được danh tính của mạng nó tuyên bố, nên chưa ký hay gửi gì.
- **Điều gì có thể đã xảy ra rồi:** Một yêu cầu đã đến dịch vụ bên ngoài; không có khoản thanh toán nào được chuyển
- **Cần làm gì tiếp theo:** Kiểm tra điểm cuối RPC của mạng; chain id hoặc genesis sai bị từ chối trước mọi khoản thanh toán.
- **Thử lại:** Đừng gửi lại cùng một yêu cầu mà không thay đổi gì.
- **Mức nghiêm trọng:** cao · **Danh mục:** bảo mật · **Liên quan đến bảo mật:** có

<a name="wallet-confirmation-required"></a>

### wallet_confirmation_required

**Cần xác nhận đã gõ**

- **Chuyện gì đã xảy ra:** Bước đó cần xác nhận đã gõ trước, nên chưa thay đổi gì. Xác nhận rồi thử lại.
- **Điều gì có thể đã xảy ra rồi:** Chưa gửi, ký hay thay đổi gì
- **Cần làm gì tiếp theo:** Đọc cảnh báo và gõ chính xác cụm từ xác nhận để tiếp tục.
- **Thử lại:** Hãy thử lại sau khi thực hiện thay đổi mà mục này nêu.
- **Mức nghiêm trọng:** thấp · **Danh mục:** chính sách

<a name="wallet-credential-mismatch"></a>

### wallet_credential_mismatch

**Các mục nhập không khớp**

- **Chuyện gì đã xảy ra:** Hai mục nhập không khớp, nên chưa tạo gì. Nhập cùng một PIN hoặc mật khẩu hai lần rồi thử lại.
- **Điều gì có thể đã xảy ra rồi:** Chưa gửi, ký hay thay đổi gì
- **Cần làm gì tiếp theo:** Thông tin xác thực Chương trình Tiền điện tử được xác nhận trước khi bất kỳ vật liệu khóa nào tồn tại.
- **Thử lại:** Hãy thử lại sau khi thực hiện thay đổi mà mục này nêu.
- **Mức nghiêm trọng:** thấp · **Danh mục:** chính sách

<a name="wallet-dependency-unavailable"></a>

### wallet_dependency_unavailable

**Chưa cài thư viện EVM**

- **Chuyện gì đã xảy ra:** Bản dựng này không ký được EVM hay mã hóa ABI: các thư viện Ethereum chưa cài trên máy này. Chưa ký hay gửi gì. Solana, lưu ký và trần chi tiêu không bị ảnh hưởng; sau khi cài thư viện, thử lại.
- **Điều gì có thể đã xảy ra rồi:** Chưa gửi, ký hay thay đổi gì
- **Cần làm gì tiếp theo:** Cài các phần bổ sung EVM (eth-abi, eth-utils, eth-account) để bật các làn EVM; mọi chức năng ví khác chạy không cần chúng.
- **Thử lại:** Hãy thử lại sau khi thực hiện thay đổi mà mục này nêu.
- **Mức nghiêm trọng:** trung bình · **Danh mục:** chính sách

<a name="wallet-device-auth-denied"></a>

### wallet_device_auth_denied

**Mở khóa bằng thiết bị bị từ chối**

- **Chuyện gì đã xảy ra:** Touch ID hoặc mật khẩu máy Mac của bạn bị từ chối hoặc không mở được ví này, nên chưa tạo, thay đổi, mở khóa, tiết lộ, ký hay gửi gì. Hãy thử lại.
- **Điều gì có thể đã xảy ra rồi:** Chưa gửi, ký hay thay đổi gì
- **Cần làm gì tiếp theo:** Hãy thử lại và xác nhận yêu cầu Touch ID hoặc mật khẩu máy Mac mà VOOL hiển thị. Ví phê duyệt bằng Touch ID không có PIN hay mật khẩu riêng để dùng thay thế. Nếu yêu cầu vẫn thất bại, hãy khôi phục ví từ cụm khôi phục của nó (Crypto Pilot thì từ bản dự phòng hiện khi thiết lập) và đặt cho nó một PIN hoặc mật khẩu; việc xuất khóa riêng vẫn cần Touch ID hoặc mật khẩu máy Mac.
- **Thử lại:** Hãy thử lại sau khi thực hiện thay đổi mà mục này nêu.
- **Mức nghiêm trọng:** thấp · **Danh mục:** chính sách

<a name="wallet-device-auth-unavailable"></a>

### wallet_device_auth_unavailable

**Không dùng được mở khóa bằng thiết bị**

- **Chuyện gì đã xảy ra:** Touch ID hoặc mật khẩu máy Mac của bạn không dùng được cho ví này ở đây, nên chưa tạo, thay đổi, mở khóa, tiết lộ, ký hay gửi gì. Hãy thử lại khi dùng được.
- **Điều gì có thể đã xảy ra rồi:** Chưa gửi, ký hay thay đổi gì
- **Cần làm gì tiếp theo:** Một ví mới có thể phê duyệt bằng PIN hoặc mật khẩu riêng của nó thay thế. Ví phê duyệt bằng Touch ID không có PIN hay mật khẩu riêng: nếu Touch ID và mật khẩu máy Mac vẫn không dùng được, hãy khôi phục ví từ cụm khôi phục của nó (Crypto Pilot thì từ bản dự phòng hiện khi thiết lập) và đặt cho nó một PIN hoặc mật khẩu. Việc xuất khóa riêng chỉ mở bằng Touch ID hoặc mật khẩu máy Mac, nên hãy khôi phục ví trên một máy Mac mà chúng hoạt động, rồi xuất ở đó.
- **Thử lại:** Hãy thử lại sau khi thực hiện thay đổi mà mục này nêu.
- **Mức nghiêm trọng:** trung bình · **Danh mục:** tính khả dụng

<a name="wallet-disabled"></a>

### wallet_disabled

**Ví đang tắt**

- **Chuyện gì đã xảy ra:** Ví đang tắt, nên chưa đề xuất hay gửi khoản thanh toán nào. Bật công tắc "Bật Tiền điện tử" trong Cài đặt, Tiền điện tử rồi thử lại.
- **Điều gì có thể đã xảy ra rồi:** Chưa gửi, ký hay thay đổi gì
- **Cần làm gì tiếp theo:** Chỉ bật công tắc "Bật Tiền điện tử" trong Cài đặt, Tiền điện tử nếu bạn muốn thanh toán trên bản cài này. Khi biến VOOL_WALLET_ENABLED được đặt, nó bật hoặc tắt ví bất kể công tắc hiển thị gì.
- **Thử lại:** Hãy thử lại sau khi thực hiện thay đổi mà mục này nêu.
- **Mức nghiêm trọng:** thấp · **Danh mục:** chính sách

<a name="wallet-duplicate-payment"></a>

### wallet_duplicate_payment

**Khoản thanh toán đã được đề xuất hoặc gửi**

- **Chuyện gì đã xảy ra:** Khoản thanh toán này đã được đề xuất hoặc gửi, nên không gửi lại.
- **Điều gì có thể đã xảy ra rồi:** Đã có một bản ghi hoặc thay đổi cục bộ
- **Cần làm gì tiếp theo:** Kiểm tra biên lai của đề xuất sẵn có; dùng khóa idempotency mới cho một khoản thực sự mới.
- **Thử lại:** Đừng gửi lại cùng một yêu cầu mà không thay đổi gì.
- **Mức nghiêm trọng:** trung bình · **Danh mục:** tính toàn vẹn · **Liên quan đến bảo mật:** có

<a name="wallet-environment-inactive"></a>

### wallet_environment_inactive

**Sai môi trường mạng**

- **Chuyện gì đã xảy ra:** Mạng đó thuộc môi trường mạng kia (Mainnet hoặc mạng Thử nghiệm), nên chưa tạo, đề xuất hay ký gì. Chuyển trong Cài đặt, Tiền điện tử, Tùy chọn nhà phát triển, rồi thử lại.
- **Điều gì có thể đã xảy ra rồi:** Chưa gửi, ký hay thay đổi gì
- **Cần làm gì tiếp theo:** Chọn môi trường mạng một cách chủ ý trong cài đặt Tiền điện tử; tài khoản, số dư và biên lai không bao giờ đi giữa các môi trường.
- **Thử lại:** Hãy thử lại sau khi thực hiện thay đổi mà mục này nêu.
- **Mức nghiêm trọng:** trung bình · **Danh mục:** bảo mật · **Liên quan đến bảo mật:** có

<a name="wallet-export-pin-not-accepted"></a>

### wallet_export_pin_not_accepted

**Việc xuất chỉ mở bằng xác thực thiết bị**

- **Chuyện gì đã xảy ra:** Việc xuất không bao giờ mở bằng PIN, mật khẩu hay cụm mật khẩu, chỉ bằng xác thực thiết bị (Touch ID hoặc mật khẩu máy Mac của bạn), nên chưa tiết lộ gì. Hãy thử lại mà không kèm PIN hay mật khẩu.
- **Điều gì có thể đã xảy ra rồi:** Chưa gửi, ký hay thay đổi gì
- **Cần làm gì tiếp theo:** Việc xuất cố ý chỉ mở bằng xác thực thiết bị; PIN, mật khẩu hay cụm mật khẩu trong yêu cầu bị từ chối trước cả khi được kiểm tra.
- **Thử lại:** Hãy thử lại sau khi thực hiện thay đổi mà mục này nêu.
- **Mức nghiêm trọng:** cao · **Danh mục:** bảo mật · **Liên quan đến bảo mật:** có

<a name="wallet-export-refused"></a>

### wallet_export_refused

**Cách xuất cũ đã bị ngừng**

- **Chuyện gì đã xảy ra:** Cách xuất khóa riêng đó đã bị ngừng, nên chưa tiết lộ gì. Ví cho phép xuất có nút "Export private key" trong Cài đặt, Tiền điện tử, và nút này chỉ mở bằng Touch ID hoặc mật khẩu máy Mac của bạn.
- **Điều gì có thể đã xảy ra rồi:** Chưa gửi, ký hay thay đổi gì
- **Cần làm gì tiếp theo:** Dùng "Export private key" trong Cài đặt, Tiền điện tử, Tài khoản khác: chỉ túi cũ mới xuất được, và chỉ bằng Touch ID hoặc mật khẩu máy Mac. Ví Crypto Pilot không bao giờ xuất; nó được khôi phục từ bản dự phòng hiện khi thiết lập.
- **Thử lại:** Đừng gửi lại cùng một yêu cầu mà không thay đổi gì.
- **Mức nghiêm trọng:** cao · **Danh mục:** bảo mật · **Liên quan đến bảo mật:** có

<a name="wallet-export-unavailable"></a>

### wallet_export_unavailable

**Không thể xuất ví này**

- **Chuyện gì đã xảy ra:** Không thể xuất ví này theo cách đó: ví chỉ-xem hoặc không giữ khóa nào trên máy này, bản dự phòng duy nhất của ví đã hiện khi thiết lập và ví không bao giờ xuất, VOOL không xuất sang ứng dụng ví bạn chọn, hoặc đây là túi cũ từ phiên bản trước, cần được khôi phục từ cụm khôi phục của nó trước khi xuất sang MetaMask. Chưa tiết lộ gì.
- **Điều gì có thể đã xảy ra rồi:** Chưa gửi, ký hay thay đổi gì
- **Cần làm gì tiếp theo:** Chỉ túi cũ được niêm phong trên máy này mới xuất được, sang Phantom hoặc MetaMask, và chỉ bằng Touch ID hoặc mật khẩu máy Mac. Ví Crypto Pilot được khôi phục từ bản dự phòng hiện khi thiết lập. Túi cũ từ phiên bản trước xuất sang MetaMask sau khi được khôi phục từ cụm khôi phục của nó.
- **Thử lại:** Đừng gửi lại cùng một yêu cầu mà không thay đổi gì.
- **Mức nghiêm trọng:** cao · **Danh mục:** bảo mật · **Liên quan đến bảo mật:** có

<a name="wallet-insufficient-funds"></a>

### wallet_insufficient_funds

**Số dư không đủ cho số tiền và phí**

- **Chuyện gì đã xảy ra:** Số dư không đủ cho số tiền cộng tối đa phí mạng có thể, nên chưa chuẩn bị gì. Hạ số tiền hoặc nạp thêm rồi thử lại.
- **Điều gì có thể đã xảy ra rồi:** Một yêu cầu đã đến dịch vụ bên ngoài; không có khoản thanh toán nào được chuyển
- **Cần làm gì tiếp theo:** Số chi được loại trừ các khoản giữ đang xử lý; phần dư Solana phải không có số 0 cuối và ít nhất bằng tiền thuê.
- **Thử lại:** Hãy thử lại sau khi thực hiện thay đổi mà mục này nêu.
- **Mức nghiêm trọng:** thấp · **Danh mục:** chính sách

<a name="wallet-legacy-surface-retired"></a>

### wallet_legacy_surface_retired

**Con đường tiền cũ đã bị ngừng**

- **Chuyện gì đã xảy ra:** Con đường tiền đó đã nghỉ hưu: mọi thanh toán đều qua luồng đề xuất và phê duyệt của ví. Chưa ký hay gửi gì.
- **Điều gì có thể đã xảy ra rồi:** Chưa gửi, ký hay thay đổi gì
- **Cần làm gì tiếp theo:** Dùng các công cụ ví (wallet.propose) hoặc API ví; trình ký, trình chi và trình xuất cũ không còn phục vụ.
- **Thử lại:** Đừng gửi lại cùng một yêu cầu mà không thay đổi gì.
- **Mức nghiêm trọng:** cao · **Danh mục:** bảo mật · **Liên quan đến bảo mật:** có

<a name="wallet-limit-exceeded"></a>

### wallet_limit_exceeded

**Vượt giới hạn chi tiêu**

- **Chuyện gì đã xảy ra:** Khoản thanh toán đó vượt một giới hạn chi tiêu, nên không được gửi.
- **Điều gì có thể đã xảy ra rồi:** Chưa gửi, ký hay thay đổi gì
- **Cần làm gì tiếp theo:** Xem lại hạn mức mỗi giao dịch, hằng ngày và mỗi đích trước khi thử lại.
- **Thử lại:** Đừng gửi lại cùng một yêu cầu mà không thay đổi gì.
- **Mức nghiêm trọng:** trung bình · **Danh mục:** chính sách · **Liên quan đến bảo mật:** có

<a name="wallet-network-disabled"></a>

### wallet_network_disabled

**Mạng chưa được tuyên bố cho ví này**

- **Chuyện gì đã xảy ra:** Mạng đó không thuộc số mạng ví này tuyên bố cho hành động này, nên chưa tạo hay gửi gì.
- **Điều gì có thể đã xảy ra rồi:** Chưa gửi, ký hay thay đổi gì
- **Cần làm gì tiếp theo:** Dùng một trong các dòng mạng đã tuyên bố; chuỗi chưa tuyên bố, biệt danh và tên na ná bị sổ đăng ký từ chối.
- **Thử lại:** Đừng gửi lại cùng một yêu cầu mà không thay đổi gì.
- **Mức nghiêm trọng:** cao · **Danh mục:** bảo mật · **Liên quan đến bảo mật:** có

<a name="wallet-not-found"></a>

### wallet_not_found

**Không nhận diện được ví, đề xuất hoặc đích đến**

- **Chuyện gì đã xảy ra:** Ví, đề xuất hoặc đích đến đó không được nhận diện, nên chưa làm gì. Kiểm tra rồi thử lại.
- **Điều gì có thể đã xảy ra rồi:** Chưa gửi, ký hay thay đổi gì
- **Cần làm gì tiếp theo:** Kiểm tra id ví, id đề xuất hoặc địa chỉ đích rồi thử lại.
- **Thử lại:** Hãy thử lại sau khi thực hiện thay đổi mà mục này nêu.
- **Mức nghiêm trọng:** thấp · **Danh mục:** chính sách

<a name="wallet-outbound-refused"></a>

### wallet_outbound_refused

**Yêu cầu ngoài các điểm cuối đã phê duyệt**

- **Chuyện gì đã xảy ra:** Yêu cầu đó không được rời khỏi các điểm cuối đã phê duyệt của ví, nên chưa kết nối.
- **Điều gì có thể đã xảy ra rồi:** Chưa gửi, ký hay thay đổi gì
- **Cần làm gì tiếp theo:** Kiểm tra các nguồn gốc RPC đã tuyên bố và chính sách tài nguyên thanh toán; máy chủ riêng tư, metadata và chưa duyệt bị từ chối.
- **Thử lại:** Đừng gửi lại cùng một yêu cầu mà không thay đổi gì.
- **Mức nghiêm trọng:** cao · **Danh mục:** bảo mật · **Liên quan đến bảo mật:** có

<a name="wallet-password-invalid"></a>

### wallet_password_invalid

**Mật khẩu ví không hợp lệ**

- **Chuyện gì đã xảy ra:** Mật khẩu không đạt chính sách hoặc không mở khóa được ví, nên chưa ký hay thay đổi gì. Kiểm tra mật khẩu rồi thử lại.
- **Điều gì có thể đã xảy ra rồi:** Chưa gửi, ký hay thay đổi gì
- **Cần làm gì tiếp theo:** Mật khẩu ví dài từ 10 đến 128 ký tự và không chỉ gồm chữ số. Mật khẩu sai giữ nguyên khóa niêm phong và được tính vào lần khóa ví sau quá nhiều lần thử sai.
- **Thử lại:** Hãy thử lại sau khi thực hiện thay đổi mà mục này nêu.
- **Mức nghiêm trọng:** thấp · **Danh mục:** chính sách

<a name="wallet-paykit-unavailable"></a>

### wallet_paykit_unavailable

**Chưa cài phần bổ sung pay cho Solana**

- **Chuyện gì đã xảy ra:** Bản dựng này không trả được các đề nghị x402 hay MPP trên Solana: phần bổ sung tùy chọn pay (Solana pay-kit, Python 3.11 trở lên) chưa cài trên máy này. Chưa ký hay gửi gì. Mọi chức năng ví khác chạy không cần nó; sau khi cài, thử lại.
- **Điều gì có thể đã xảy ra rồi:** Chưa gửi, ký hay thay đổi gì
- **Cần làm gì tiếp theo:** Cài phần bổ sung tùy chọn pay của VOOL trên Python 3.11 trở lên để bật thanh toán pay-kit; dù thế nào ví vẫn giữ bộ ký riêng, trần chi tiêu và bước phê duyệt của mình.
- **Thử lại:** Hãy thử lại sau khi thực hiện thay đổi mà mục này nêu.
- **Mức nghiêm trọng:** trung bình · **Danh mục:** chính sách

<a name="wallet-payment-channel-off"></a>

### wallet_payment_channel_off

**Kênh thanh toán đang tắt**

- **Chuyện gì đã xảy ra:** Khoản thanh toán đó đi qua một kênh thanh toán đang tắt, nên chưa ký hay gửi gì. Bật kênh trong Cài đặt, Tiền điện tử, Kênh thanh toán, rồi thử lại.
- **Điều gì có thể đã xảy ra rồi:** Chưa gửi, ký hay thay đổi gì
- **Cần làm gì tiếp theo:** Chỉ bật một kênh thanh toán trong cài đặt Tiền điện tử nếu bạn muốn thanh toán qua kênh đó; trần chi tiêu và sự phê duyệt của bạn áp dụng trên mọi kênh.
- **Thử lại:** Hãy thử lại sau khi thực hiện thay đổi mà mục này nêu.
- **Mức nghiêm trọng:** thấp · **Danh mục:** chính sách

<a name="wallet-pin-invalid"></a>

### wallet_pin_invalid

**PIN không hợp lệ**

- **Chuyện gì đã xảy ra:** PIN không đạt chính sách hoặc không mở được ví, nên chưa ký gì. Kiểm tra PIN rồi thử lại.
- **Điều gì có thể đã xảy ra rồi:** Chưa gửi, ký hay thay đổi gì
- **Cần làm gì tiếp theo:** Dùng PIN 6 đến 12 chữ số; PIN sai để nguyên khóa niêm phong.
- **Thử lại:** Hãy thử lại sau khi thực hiện thay đổi mà mục này nêu.
- **Mức nghiêm trọng:** thấp · **Danh mục:** chính sách

<a name="wallet-quote-expired"></a>

### wallet_quote_expired

**Bản xem trước đã hết hạn hoặc bị thay thế**

- **Chuyện gì đã xảy ra:** Bản xem trước chuyển khoản này đã hết hạn hoặc bị thay thế nên không thể phê duyệt và chưa gửi gì. Làm mới bản xem trước rồi thử lại.
- **Điều gì có thể đã xảy ra rồi:** Chưa gửi, ký hay thay đổi gì
- **Cần làm gì tiếp theo:** Một báo giá sống ngắn và bị thay bởi làm mới hoặc đổi môi trường; phê duyệt luôn buộc báo giá hiện hành.
- **Thử lại:** Hãy thử lại sau khi thực hiện thay đổi mà mục này nêu.
- **Mức nghiêm trọng:** thấp · **Danh mục:** chính sách

<a name="wallet-quote-mismatch"></a>

### wallet_quote_mismatch

**Phê duyệt không còn khớp với bản xem trước**

- **Chuyện gì đã xảy ra:** Phê duyệt không còn khớp với chuyển khoản đã xem trước, nên chưa ký hay gửi gì. Bắt đầu lại từ bản xem trước mới.
- **Điều gì có thể đã xảy ra rồi:** Chưa gửi, ký hay thay đổi gì
- **Cần làm gì tiếp theo:** Phê duyệt buộc mã băm của một báo giá; tài khoản, chuỗi, người nhận, số tiền, môi trường hay phí đổi là phê duyệt mới.
- **Thử lại:** Đừng gửi lại cùng một yêu cầu mà không thay đổi gì.
- **Mức nghiêm trọng:** cao · **Danh mục:** bảo mật · **Liên quan đến bảo mật:** có

<a name="wallet-quote-unavailable"></a>

### wallet_quote_unavailable

**Không rõ số dư hoặc phí**

- **Chuyện gì đã xảy ra:** Mạng chưa trả lời trọn vẹn về số dư hoặc phí của chuyển khoản này, nên chưa chuẩn bị phê duyệt và chưa gửi gì. Thử lại sau ít phút.
- **Điều gì có thể đã xảy ra rồi:** Một yêu cầu đã đến dịch vụ bên ngoài; không có khoản thanh toán nào được chuyển
- **Cần làm gì tiếp theo:** Dữ liệu chuỗi không rõ không bao giờ coi là không; kiểm tra điểm cuối của dòng và bằng chứng danh tính của nó.
- **Thử lại:** Hãy thử lại sau: điều đã chặn việc này sẽ tự hết theo thời gian.
- **Mức nghiêm trọng:** trung bình · **Danh mục:** tính khả dụng

<a name="wallet-recipient-refused"></a>

### wallet_recipient_refused

**Người nhận không thể an toàn nhận khoản này**

- **Chuyện gì đã xảy ra:** Người nhận đó không thể an toàn nhận chuyển khoản này trên mạng này, nên chưa chuẩn bị gì. Kiểm tra địa chỉ rồi thử lại.
- **Điều gì có thể đã xảy ra rồi:** Chưa gửi, ký hay thay đổi gì
- **Cần làm gì tiếp theo:** Từ chối: tài khoản Solana mới dưới mức thuê tối thiểu, tài khoản thực thi hoặc ngoài đường cong, tổng kiểm tra EIP-55 sai, địa chỉ số không, hợp đồng biên dịch sẵn và hợp đồng hệ thống.
- **Thử lại:** Hãy thử lại sau khi thực hiện thay đổi mà mục này nêu.
- **Mức nghiêm trọng:** trung bình · **Danh mục:** chính sách

<a name="wallet-recovery-refused"></a>

### wallet_recovery_refused

**Bằng chứng khôi phục không khớp ví này**

- **Chuyện gì đã xảy ra:** Bằng chứng khôi phục đó không khớp ví này, nên chưa thay đổi gì: khóa đã lưu phải là bản dự phòng VOOL đã hiển thị cho đúng địa chỉ này, đúng định dạng đã hiển thị. Kiểm tra rồi thử lại, hoặc dùng các tùy chọn khác.
- **Điều gì có thể đã xảy ra rồi:** Chưa gửi, ký hay thay đổi gì
- **Cần làm gì tiếp theo:** Phục hồi niêm phong lại chỉ một ví sẵn có và chỉ từ bản dự phòng giải mã ra đúng khóa của ví đó (cả hai chiều).
- **Thử lại:** Hãy thử lại sau khi thực hiện thay đổi mà mục này nêu.
- **Mức nghiêm trọng:** cao · **Danh mục:** bảo mật · **Liên quan đến bảo mật:** có

<a name="wallet-request-ambiguous"></a>

### wallet_request_ambiguous

**Có nhiều hơn một tài khoản hoặc mạng khớp**

- **Chuyện gì đã xảy ra:** Nhiều hơn một tài khoản hoặc mạng khớp với chuyển khoản này, nên chưa đề xuất gì. Nói rõ tài khoản hoặc mạng bạn muốn rồi thử lại.
- **Điều gì có thể đã xảy ra rồi:** Chưa gửi, ký hay thay đổi gì
- **Cần làm gì tiếp theo:** Một lần chuyển nêu một tài khoản trên một mạng; các ứng viên được liệt kê trong ngữ cảnh lỗi.
- **Thử lại:** Hãy thử lại sau khi thực hiện thay đổi mà mục này nêu.
- **Mức nghiêm trọng:** thấp · **Danh mục:** chính sách

<a name="wallet-security-key-needed"></a>

### wallet_security_key_needed

**Cần khóa bảo mật**

- **Chuyện gì đã xảy ra:** Ví này còn cần khóa bảo mật của nó, nhưng khóa không phản hồi: khóa chưa được cắm, là một khóa khác, hoặc chưa được chạm kịp lúc. Không có gì được mở khóa, thay đổi, ký hay gửi đi. Hãy cắm khóa bảo mật của ví này, chạm vào khi khóa nhấp nháy rồi thử lại.
- **Điều gì có thể đã xảy ra rồi:** Chưa gửi, ký hay thay đổi gì
- **Cần làm gì tiếp theo:** Hãy cắm khóa bảo mật đã thêm vào ví này và chạm vào khi khóa nhấp nháy. Nếu mất khóa, hãy khôi phục ví bằng khóa sao lưu của ví (ghi trên giấy, trong Chuỗi khóa hoặc trên USB): việc này đặt PIN mới và gỡ khóa đã mất khỏi ví.
- **Thử lại:** Hãy thử lại sau khi thực hiện thay đổi mà mục này nêu.
- **Mức nghiêm trọng:** thấp · **Danh mục:** tính khả dụng

<a name="wallet-send-needs-vool-wallet"></a>

### wallet_send_needs_vool_wallet

**Cần VOOL Wallet để gửi**

- **Chuyện gì đã xảy ra:** Tài khoản này không thể gửi từ VOOL trên mạng này: ở đây chỉ VOOL Wallet tạo trên thiết bị này mới gửi được. Tài khoản đã kết nối hoặc chỉ-xem vẫn có thể nhận và hiển thị số dư. Chưa ký hay gửi gì. Để gửi, hãy chọn một VOOL Wallet trên thiết bị này rồi thử lại.
- **Điều gì có thể đã xảy ra rồi:** Chưa gửi, ký hay thay đổi gì
- **Cần làm gì tiếp theo:** Hãy gửi từ một VOOL Wallet trên thiết bị này, hoặc từ chính ứng dụng của ví đã kết nối; trên mạng này VOOL không ký gì cho tài khoản đã kết nối hoặc chỉ-xem.
- **Thử lại:** Hãy thử lại sau khi thực hiện thay đổi mà mục này nêu.
- **Mức nghiêm trọng:** thấp · **Danh mục:** chính sách

<a name="wallet-setup-state-invalid"></a>

### wallet_setup_state_invalid

**Bước thiết lập hiện không áp dụng**

- **Chuyện gì đã xảy ra:** Bước thiết lập đó hiện không áp dụng cho ví này, nên chưa thay đổi gì. Kiểm tra trạng thái thiết lập của ví rồi thử lại.
- **Điều gì có thể đã xảy ra rồi:** Chưa gửi, ký hay thay đổi gì
- **Cần làm gì tiếp theo:** Thiết lập đi theo: tạo, chờ dự phòng, dự phòng đã tiết lộ, sẵn sàng; hủy và tiếp tục không bao giờ tiết lộ dự phòng hai lần.
- **Thử lại:** Hãy thử lại sau khi thực hiện thay đổi mà mục này nêu.
- **Mức nghiêm trọng:** thấp · **Danh mục:** chính sách

<a name="wallet-signature-invalid"></a>

### wallet_signature_invalid

**Chữ ký không khớp khóa của ví**

- **Chuyện gì đã xảy ra:** Chữ ký trả về cho khoản thanh toán này không khớp khóa của ví, nên không được phát thanh.
- **Điều gì có thể đã xảy ra rồi:** Một yêu cầu đã đến dịch vụ bên ngoài; không có khoản thanh toán nào được chuyển
- **Cần làm gì tiếp theo:** Kiểm tra trình ký đã kết nối; chữ ký lệch bị từ chối trước khi phát thanh.
- **Thử lại:** Đừng gửi lại cùng một yêu cầu mà không thay đổi gì.
- **Mức nghiêm trọng:** cao · **Danh mục:** bảo mật · **Liên quan đến bảo mật:** có

<a name="wallet-signing-unavailable"></a>

### wallet_signing_unavailable

**Ví không thể ký từ đây**

- **Chuyện gì đã xảy ra:** Ví này không thể ký từ đây: nó chỉ-xem hoặc trình ký ngoài chưa kết nối. Chưa gửi gì; kết nối trình ký rồi thử lại.
- **Điều gì có thể đã xảy ra rồi:** Chưa gửi, ký hay thay đổi gì
- **Cần làm gì tiếp theo:** Kết nối ví ngoài, hoặc tạo Ví VOOL trên mạng đó trong Cài đặt, Tiền điện tử.
- **Thử lại:** Hãy thử lại sau khi thực hiện thay đổi mà mục này nêu.
- **Mức nghiêm trọng:** trung bình · **Danh mục:** chính sách

<a name="wallet-simulation-failed"></a>

### wallet_simulation_failed

**Khoản thanh toán không qua mô phỏng**

- **Chuyện gì đã xảy ra:** Khoản thanh toán không qua mô phỏng, nên không được gửi. Kiểm tra số dư và đích đến rồi thử lại.
- **Điều gì có thể đã xảy ra rồi:** Một yêu cầu đã đến dịch vụ bên ngoài; không có khoản thanh toán nào được chuyển
- **Cần làm gì tiếp theo:** Kiểm tra số dư, đích đến và điểm cuối RPC của mạng, rồi đề xuất lại.
- **Thử lại:** Hãy thử lại sau: điều đã chặn việc này sẽ tự hết theo thời gian.
- **Mức nghiêm trọng:** trung bình · **Danh mục:** tính khả dụng

<a name="wallet-storage-class-refused"></a>

### wallet_storage_class_refused

**Lưu khóa chỉ trong bộ nhớ**

- **Chuyện gì đã xảy ra:** Bản cài này giữ khóa thiết bị chỉ trong bộ nhớ, nên ví tạo ở đây sẽ không mở được sau khi khởi động lại. Chưa tạo gì. Chuyển lưu khóa sang chế độ bền rồi thử lại.
- **Điều gì có thể đã xảy ra rồi:** Chưa gửi, ký hay thay đổi gì
- **Cần làm gì tiếp theo:** Ví Chương trình Tiền điện tử bị từ chối khi VOOL_KEY_STORAGE_MODE là ephemeral; dùng lưu trữ file hoặc keyring.
- **Thử lại:** Hãy thử lại sau khi thực hiện thay đổi mà mục này nêu.
- **Mức nghiêm trọng:** trung bình · **Danh mục:** chính sách

<a name="wallet-unlock-throttled"></a>

### wallet_unlock_throttled

**Ví bị khóa sau các lần mở khóa thất bại**

- **Chuyện gì đã xảy ra:** Có quá nhiều lần gõ PIN hoặc mật khẩu sai, nên ví này bị khóa một lúc và chưa mở khóa được. Chờ rồi thử lại.
- **Điều gì có thể đã xảy ra rồi:** Chưa gửi, ký hay thay đổi gì
- **Cần làm gì tiếp theo:** Các lần mở khóa thất bại được đếm bền vững theo mỗi ví và cho cả ứng dụng, qua các lần khởi động lại; khóa tự nhấc.
- **Thử lại:** Hãy thử lại sau: điều đã chặn việc này sẽ tự hết theo thời gian.
- **Mức nghiêm trọng:** cao · **Danh mục:** bảo mật · **Liên quan đến bảo mật:** có

<a name="wallet-x402-cap-exceeded"></a>

### wallet_x402_cap_exceeded

**Tài nguyên trả phí vượt mức trần thanh toán tự động**

- **Chuyện gì đã xảy ra:** Một tài nguyên trả phí yêu cầu nhiều hơn mức trần thanh toán tự động, nên không được trả.
- **Điều gì có thể đã xảy ra rồi:** Chưa gửi, ký hay thay đổi gì
- **Cần làm gì tiếp theo:** Chủ ý nâng VOOL_WALLET_X402_CAP_MINOR, hoặc trả tài nguyên bằng một đề xuất tường minh.
- **Thử lại:** Đừng gửi lại cùng một yêu cầu mà không thay đổi gì.
- **Mức nghiêm trọng:** trung bình · **Danh mục:** chính sách · **Liên quan đến bảo mật:** có

<a name="wallet-x402-sdk-unavailable"></a>

### wallet_x402_sdk_unavailable

**Chưa cài phần bổ sung x402**

- **Chuyện gì đã xảy ra:** Bản dựng này không trả được các đề nghị x402 trên Base qua x402 Foundation SDK: phần bổ sung tùy chọn x402 chưa cài trên máy này. Chưa ký hay gửi gì. Mọi chức năng ví khác chạy không cần nó; sau khi cài, thử lại.
- **Điều gì có thể đã xảy ra rồi:** Chưa gửi, ký hay thay đổi gì
- **Cần làm gì tiếp theo:** Cài phần bổ sung tùy chọn x402 của VOOL để trả các đề nghị x402 trên Base từ ví Crypto Pilot; dù thế nào ví vẫn giữ bộ ký riêng, trần chi tiêu và bước phê duyệt của mình.
- **Thử lại:** Hãy thử lại sau khi thực hiện thay đổi mà mục này nêu.
- **Mức nghiêm trọng:** trung bình · **Danh mục:** chính sách

<a name="x402-scheme-unavailable"></a>

### x402_scheme_unavailable

**Lược đồ thanh toán không được hỗ trợ**

- **Chuyện gì đã xảy ra:** Đề nghị thanh toán này cần một lược đồ mà ví này không thể chứng minh đầu-cuối, nên chưa ký gì. Thử một đề nghị có lược đồ, token và bên trung gian được xác minh cho mạng này.
- **Điều gì có thể đã xảy ra rồi:** Chưa gửi, ký hay thay đổi gì
- **Cần làm gì tiếp theo:** Dùng một đề nghị có lược đồ, token và bên trung gian được xác minh cho mạng; hỗ trợ ký một phần không đủ ở đây.
- **Thử lại:** Hãy thử lại sau khi thực hiện thay đổi mà mục này nêu.
- **Mức nghiêm trọng:** thấp · **Danh mục:** chính sách

---
version: 1.0-draft
title: Điều khoản cam kết sử dụng chữ ký số nội bộ
---

**[BẢN NHÁP — CẦN BỘ PHẬN PHÁP CHẾ RÀ SOÁT VÀ PHÊ DUYỆT TRƯỚC KHI BAN HÀNH CHÍNH THỨC]**

Văn bản này quy định nguyên tắc, quyền hạn và trách nhiệm của cán bộ, nhân viên (sau đây gọi là **"Người sử dụng"**) khi được cấp và sử dụng chứng thư số nội bộ, mã PIN ký số và tài khoản trên hệ thống **esign** của Công ty.

### Điều 1. Phạm vi và mục đích sử dụng
1. Hệ thống chữ ký số nội bộ **esign** được vận hành nhằm phục vụ công tác trình ký, phê duyệt, xác nhận văn bản và hồ sơ lưu hành **trong phạm vi nội bộ Công ty**.
2. Chứng thư số nội bộ do Cơ quan Chứng thực Nội bộ (Internal CA) của Công ty cấp phát, không thay thế chữ ký số công cộng trong các giao dịch bắt buộc có giá trị pháp lý bên ngoài theo quy định của pháp luật, trừ khi được quy định riêng bằng văn bản của Ban Lãnh đạo.
3. Dấu thời gian (TSA) và Danh sách thu hồi chứng thư (CRL) nội bộ được sử dụng để kiểm chứng tính toàn vẹn và thời điểm ký của tài liệu trên các trình đọc PDF chuẩn (Adobe Acrobat, Foxit PDF Reader) đã cài đặt Root CA nội bộ.

### Điều 2. Bảo mật tài khoản, xác thực hai lớp (2FA) và mã PIN ký số
1. Người sử dụng có trách nhiệm bảo mật tuyệt đối tên đăng nhập, mật khẩu, thiết bị/mã khôi phục xác thực hai lớp (TOTP 2FA) và mã **PIN ký số 6 chữ số** của mình.
2. Không sử dụng các mã PIN dễ đoán (chuỗi tăng/giảm liên tiếp, số lặp, trùng mật khẩu đăng nhập) và không chia sẻ, ghi chép công khai hoặc để người khác biết mã PIN ký số.
3. Trong trường hợp rời khỏi vị trí làm việc, Người sử dụng phải khóa màn hình máy tính hoặc đăng xuất khỏi hệ thống; tuyệt đối không nhờ hoặc ủy quyền cho cá nhân khác thực hiện thao tác nhập PIN thay mình dưới bất kỳ hình thức nào.

### Điều 3. Trách nhiệm xác nhận nội dung và tính không thể phủ nhận
1. Trước khi nhập mã PIN để ký, từ chối hoặc hủy một luồng ký tài liệu, Người sử dụng phải kiểm tra đầy đủ nội dung văn bản PDF, trang ký, vị trí hiển thị chữ ký và ghi chú/lý do đính kèm.
2. Mọi tài liệu được ký hợp lệ bằng chứng thư số gắn với tài khoản của Người sử dụng cùng xác thực 2FA và mã PIN ký được xem là hành vi xác nhận ý chí chính thức của chính Người sử dụng đó trong quy trình nội bộ Công ty; Người sử dụng không được phủ nhận trách nhiệm đối với chữ ký đã tạo ra trừ khi chứng minh được hệ thống bị xâm nhập do lỗi hạ tầng ngoài tầm kiểm soát và đã báo cáo sự cố kịp thời.

### Điều 4. Báo cáo sự cố và thu hồi chứng thư số
1. Khi phát hiện hoặc nghi ngờ lộ mật khẩu, mất thiết bị xác thực TOTP, lộ mã PIN ký hoặc có dấu hiệu truy cập trái phép, Người sử dụng phải lập tức thay đổi PIN/mật khẩu (nếu còn quyền truy cập) và báo ngay cho **Quản trị viên hệ thống / Bộ phận CNTT** để tạm khóa tài khoản và **thu hồi (revoke) chứng thư số trên CRL**.
2. Khi Người sử dụng chuyển công tác, thay đổi chức danh/phòng ban hoặc chấm dứt hợp đồng lao động, bộ phận Quản trị có quyền vô hiệu hóa tài khoản và thu hồi chứng thư số tương ứng theo quy trình nhân sự của Công ty.

### Điều 5. Nhật ký kiểm toán (Audit Log) và bảo vệ dữ liệu
1. Mọi thao tác trên hệ thống (đăng nhập, xác thực PIN, đồng ý điều khoản, tạo tài liệu, mở xem, ký số, từ chối, hủy luồng, thay đổi chứng thư) đều được ghi nhận tự động vào hệ thống nhật ký kiểm toán chỉ ghi thêm (append-only) kèm thời điểm UTC, địa chỉ IP và định danh phiên làm việc để phục vụ tra soát, đối chiếu khi cần thiết.
2. Hệ thống không lưu giữ mã PIN ở dạng văn bản rõ và chỉ sử dụng thông tin cá nhân (Họ tên, Chức danh, Phòng ban, Email, chữ ký mẫu) vào mục đích hiển thị trên luồng ký, chứng thư số nội bộ và gửi thông báo công việc.

### Điều 6. Hiệu lực cam kết
1. Bản cam kết này có hiệu lực kể từ thời điểm Người sử dụng cuộn đọc hết nội dung, đánh dấu đồng ý và xác thực thành công bằng mã OTP/TOTP.
2. Khi Công ty cập nhật phiên bản điều khoản mới, Người sử dụng có nghĩa vụ đọc và xác nhận lại ở lần đăng nhập kế tiếp để tiếp tục sử dụng chức năng ký số nội bộ.

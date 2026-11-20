# W8 – Workflow Builder, Quản lý Thành viên & Thư viện Mẫu

Milestone W8 bổ sung giao diện và API thiết kế, chỉnh sửa cấu trúc quy trình (`workflows`), quản lý phân quyền/thành viên stage và thư viện 5 mẫu quy trình nội bộ (hỗ trợ lưu nháp, nhân bản, đóng/mở lại và tạo quy trình từ mẫu), cùng các quy tắc bảo vệ dữ liệu lịch sử khi đã có `task`/`todo`.

---

## 1. Migration & Mô hình dữ liệu (idempotent)

Chạy tự động qua `migrate_db()` trong [models.py](backend/app/models.py) mà không phá vỡ dữ liệu W1–W7:

- Bảng `stages`: thêm cột `is_active BOOLEAN NOT NULL DEFAULT TRUE`.
- Bảng `form_fields`: thêm cột `is_active BOOLEAN NOT NULL DEFAULT TRUE`.
- Bảng mới `templates` (`WorkflowTemplate`): `id`, `slug` (unique), `name`, `category`, `description`, `definition` (JSON), `is_builtin`, `creator_id`, `created_at`.

---

## 2. Quy tắc sửa cấu trúc khi đã có Task đang chạy / lịch sử

Server thực thi toàn bộ trong [wf_builder.py](backend/app/wf_builder.py) và [routes_wfengine.py](backend/app/routes_wfengine.py) (`supervisor` bị chặn `403`, chỉ workflow `admin` / system `admin` được chỉnh sửa; mọi thay đổi ghi `audit_logs` kèm `workflow_id`, actor và `diff`):

| Hành động sửa cấu trúc | Khi có `task` đang `processing` | Khi chỉ còn lịch sử (`completed`/`failed`) | Không có dữ liệu lịch sử |
| --- | --- | --- | --- |
| Đổi tên, `description`, `guide_html`, `visibility` | Cho phép (HTML sanitize allowlist) | Cho phép | Cho phép |
| Đổi `sla_hours` hoặc `stage_members` | Cho phép (`sla_hours` mới chỉ áp dụng cho `todo` sinh sau thời điểm đổi) | Cho phép | Cho phép |
| Gỡ thành viên đang có `todo` `doing` ở stage | Chặn `409` | Cho phép (trừ thành viên cuối nếu active) | Cho phép |
| Thêm stage vào cuối quy trình | Cho phép | Cho phép | Cho phép |
| Chèn stage vào giữa quy trình | Chặn `409` nếu có `task` `processing` ở stage từ vị trí chèn trở đi; ngược lại cho phép | Cho phép | Cho phép |
| Xóa / Vô hiệu stage | Chặn `409` nếu có `task` `processing` ở stage đó | Chuyển thành `is_active=false` (giữ lịch sử, ẩn khỏi Board mới) | Xóa hẳn và đánh số lại `order` `1..N` |
| Đổi thứ tự stage / đổi `stage.type` | Chặn `409` | Cho phép | Cho phép |
| Thêm `form_field` mới / sửa `label`, `placeholder`, `required`, `options` | Cho phép (`required` mới chỉ áp dụng cho `task` tạo sau) | Cho phép | Cho phép |
| Đổi `form_field.type` hoặc xóa `form_field` | Chặn `409` nếu đã có dữ liệu trên `task` cũ (dùng `is_active=false` để ẩn khỏi form mới nhưng giữ nguyên trên `task` cũ) | Giống bên trái | Cho phép |
| Đóng / Mở lại quy trình | Đóng (`status=closed`) chặn tạo `task` mới (`409`), `task` đang chạy vẫn chạy xong; mở lại khôi phục tạo `task` | Cho phép | Cho phép |

---

## 3. Thư viện 5 mẫu quy trình nội bộ

Seed idempotent khi khởi động ứng dụng (`seed_builtin_templates` trong [wf_templates.py](backend/app/wf_templates.py)):

1. **`tpl-cong-van-den`** (*Công văn đến*, nhóm `Hành chính - Văn thư`): 5 bước (`normal` → `approve` → `normal` → `sign` → `normal`), 5 trường biểu mẫu.
2. **`tpl-cong-van-di`** (*Công văn đi*, nhóm `Hành chính - Văn thư`): 4 bước (`normal` → `approve` → `sign` → `normal`), 5 trường biểu mẫu.
3. **`tpl-cham-cong`** (*Chấm công*, nhóm `Nhân sự`): 3 bước (`normal` → `approve` → `approve`), 3 trường biểu mẫu.
4. **`tpl-trinh-ky-hop-dong-ho-so`** (*Trình ký hợp đồng / hồ sơ*, nhóm `Dự án - Tư vấn`): 5 bước (`normal` → `approve` → `approve` → `sign` → `normal`), 6 trường biểu mẫu.
5. **`tpl-phe-duyet-thanh-toan`** (*Phê duyệt thanh toán*, nhóm `Tài chính - Kế toán`): 4 bước (`normal` → `approve` → `sign` → `normal`), 5 trường biểu mẫu.

### Cách thêm mẫu mới

Thêm một dict vào tuple `BUILTIN_TEMPLATES` trong [wf_templates.py](backend/app/wf_templates.py) với các khóa `slug`, `name`, `category`, `description`, `definition` (`visibility`, `guide_html`, `stages`, `form_fields`, `failure_reasons`) — mọi phần tử đều đi qua `validate_template_definition()` trước khi seed. Người dùng cũng có thể lưu trực tiếp từ bất kỳ quy trình nào qua **Lưu làm mẫu** (`POST /api/workflows/{id}/save-as-template`).

---

## 4. API W8 mới bổ sung

- `GET /api/templates`, `GET /api/templates/{id}` — Danh sách & chi tiết mẫu quy trình.
- `POST /api/workflows/from-template` — Khởi tạo quy trình từ mẫu (trả thêm `warnings` không chặn cứng nếu bước `sign` thiếu thành viên/cert/PIN).
- `POST /api/workflows/{id}/close`, `POST /api/workflows/{id}/reopen` — Đóng / mở lại quy trình.
- `POST /api/workflows/{id}/clone` — Nhân bản cấu trúc quy trình (không copy `task`/`todo`).
- `POST /api/workflows/{id}/save-as-template` — Lưu cấu trúc quy trình đang hoạt động thành mẫu tùy biến.
- API chi tiết từng phần cho admin quy trình:
  - `POST /api/workflows/{id}/stages`, `PUT /api/workflows/{id}/stages/reorder`, `PATCH /api/workflows/{id}/stages/{stage_id}`, `DELETE /api/workflows/{id}/stages/{stage_id}`
  - `POST /api/workflows/{id}/stages/{stage_id}/members`, `DELETE /api/workflows/{id}/stages/{stage_id}/members/{user_id}`
  - `POST /api/workflows/{id}/form-fields`, `PATCH /api/workflows/{id}/form-fields/{field_id}`, `DELETE /api/workflows/{id}/form-fields/{field_id}`
  - `PUT /api/workflows/{id}/roles`, `POST /api/workflows/{id}/roles`, `DELETE /api/workflows/{id}/roles/{user_id}/{role}`
  - `POST /api/workflows/{id}/failure-reasons`, `PATCH /api/workflows/{id}/failure-reasons/{reason_id}`, `DELETE /api/workflows/{id}/failure-reasons/{reason_id}`

---

## 5. Giao diện W8 (Frontend)

- `/workflows`: nút **+ Tạo quy trình** và **Thư viện mẫu** cho system admin; modal `TemplateGalleryModal` xem trước cấu trúc, SLA, trường form, hướng dẫn an toàn và nạp vào Builder.
- `/workflows/new` & `/workflows/:id/edit` (`WorkflowBuilderView`): chỉnh sửa thông tin chung, `RichTextEditor` (allowlist + `<SafeHtml />`), `StageBuilder` (tổng SLA, ↑/↓/chèn sau/xóa hoặc vô hiệu, cấu hình `normal`/`approve`/`sign`, `UserPicker`, cảnh báo thiếu cert/PIN không chặn lưu), `FormFieldBuilder` (7 kiểu + xem trước form live), vai trò và lý do thất bại; hiển thị lỗi `409`/`400` trực tiếp từ server.
- `/workflows/:id/people` (`WorkflowPeopleView`): quản lý vai trò quy trình và thành viên từng giai đoạn; `supervisor` ở chế độ chỉ đọc.
- Menu `⋯` trên Board (`WorkflowBoardView`): bổ sung **Chỉnh sửa quy trình**, **Nhân bản**, **Lưu làm mẫu**, **Đóng quy trình** / **Mở lại quy trình** khi `can_manage=true`.

---

## 6. Kiểm thử

```powershell
# Frontend unit test & build
Push-Location frontend; npm.cmd test; npm.cmd run build; Pop-Location

# W8 Backend + W8/W7 Playwright + Static Security
pytest backend/tests/test_w8_builder_templates.py backend/tests/test_w8_builder_playwright.py backend/tests/test_w7_wfengine_playwright.py backend/tests/test_e2e_playwright.py::test_frontend_static_security_checks -v
```

# W7 — Frontend cho Workflow Engine

Giao diện frontend (React + TypeScript + Vite) cho Workflow Engine W5–W6, giữ nguyên toàn bộ các màn hình/tab ký tài liệu W1–W4, tuân thủ nghiêm ngặt chính sách bảo mật CSP/SPA (không `dangerouslySetInnerHTML`, `innerHTML`, `localStorage`, `sessionStorage`, hoặc URL ngoài).

---

## 1. Điều hướng & Route ([App.tsx](frontend/src/App.tsx), [router.ts](frontend/src/lib/router.ts))

Bổ sung nhóm **"Quy trình"** trong sidebar trái (kèm nút menu trượt cho màn hình mobile hẹp) cùng router History API giữ nguyên các tab W1–W4 (`Ký số PDF`, `Luồng ký`, `Tài liệu`, `Xác minh`, `Tài khoản`, `Quản trị`):

| Route | Màn hình | Chức năng chính |
| --- | --- | --- |
| `/jobs` | `JobsView` | Tab vai trò (`Được giao`, `Tạo bởi tôi`, `Tôi theo dõi`, `Nhân viên của tôi`, `Tất cả`), pill trạng thái, tìm kiếm, `FilterDrawer`, Xuất CSV/Excel (`/api/jobs/export`), phân trang, đồng bộ URL query |
| `/todos` | `TodosView` | Tab trạng thái (`Tất cả`, `Đang làm`, `Đã hoàn thành`, `Quá hạn`), chuyển vai trò, bảng 6 cột (*Công việc, Kết quả, Giao cho, Thời hạn, Nhiệm vụ liên kết, Quy trình*) |
| `/workflows` | `WorkflowsListView` | Tab `Đang khả dụng` / `Đã đóng` / `Tạo bởi tôi`, nhóm card theo phân loại (`category`), thống kê tổng/hoàn thành/thất bại/quá hạn + `ProgressBar` |
| `/workflows/:id` | `WorkflowBoardView` | Bảng Kanban các stage theo thứ tự + 2 cột hệ thống (`Hoàn thành`, `Thất bại`), **không kéo-thả**, header stage bấm để lọc sang tab `Danh sách`, `Tạo nhiệm vụ`, menu `⋯` (*Thành viên, Lưu trữ, Hướng dẫn*) |
| `/workflows/:id/archive` | `WorkflowBoardView` (`archiveMode`) | Danh sách nhiệm vụ đã lưu trữ của quy trình + nút `Khôi phục` |
| `/tasks/:id` | `TaskDetailView` | Chi tiết nhiệm vụ, đếm ngược SLA, `custom_values` theo `form_fields`, mô tả an toàn, người theo dõi, tab *Tài liệu & Ký* (xem/tải/xác minh PDF & danh sách chữ ký), tab *Lịch sử* hợp nhất (`/api/tasks/{id}/timeline`), hành động theo `stage.type` |

---

## 2. Component & Module dùng chung

- **Thư viện ([frontend/src/lib/](frontend/src/lib/)):**
  - `wfApi.ts`: kiểu dữ liệu W5–W6, định dạng ngày/SLA countdown, đọc JSON chữ ký ở `todo.result`, kiểm tra form động phía client, tải CSV, tổng hợp thống kê board.
  - `router.ts`: `parseRoute`, `navigateTo`, `parseJobsQuery`, `serializeJobsQuery`, `countActiveJobFilters`.
  - `usePolling.ts`: `useVisibilityPolling` (polling 25–30s cho board, danh sách, chi tiết và chuông thông báo; tự dừng khi tab bị ẩn và cập nhật lại khi tab hiển thị).
  - `api.ts`: mở rộng `NotificationItem` thêm `task_id` và các loại thông báo `task_assigned`, `task_completed`, `task_failed`, `task_created`, `task_edited`, `follower_added`.
- **Component ([frontend/src/components/wf/](frontend/src/components/wf/)):**
  - `Topbar.tsx`: cấu hình được tiêu đề, nút quay lại, view tabs, bộ đếm, ô tìm kiếm, nút `Bộ lọc (n)`, nút hành động phụ/chính và menu `⋯`.
  - `StatusPill.tsx`, `EmptyState.tsx`, `ProgressBar.tsx`, `LabelInput.tsx` (`Enter` tạo chip), `DateRangePicker.tsx`, `UserPicker.tsx`, `FileUploader.tsx` (tối đa 10 tệp).
  - `StageColumn.tsx` (hiển thị `"x/y NV · n Q.hạn"`, SLA `⏱ {n}h`, badge loại stage, avatar thành viên) & `TaskCard.tsx`.
  - `CompleteTodoForm.tsx`, `FailTaskForm.tsx`, `ActivityTimeline.tsx`, `FilterDrawer.tsx`, `CreateTaskModal.tsx`, `TaskSignPanel.tsx` (tái sử dụng `PdfPreviewBox` và `PinModal`).
  - `SafeHtml.tsx`: parse & render HTML bằng `DOMParser` + allowlist thẻ an toàn (`p, br, b, strong, i, em, u, s, del, ul, ol, li, blockquote, pre, code, h1..h4, span, div, a, hr`), chỉ cho phép URL hợp lệ (`http:` / `https:` / `mailto:` / đường dẫn nội bộ).
- **Thành phần W1–W4 được cập nhật:**
  - `NotificationBell.tsx`: badge tiếng Việt cho các thông báo workflow mới, chuyển thẳng tới `/tasks/{task_id}` khi có `task_id`, dùng `useVisibilityPolling`.
  - `AdminView.tsx`: bắt mã `409` khi xóa user đã có lịch sử quy trình/ký, hiển thị gợi ý và nút **Vô hiệu hóa** trực tiếp thay cho xóa vĩnh viễn.

---

## 3. Lưu ý để lại cho W8 (Workflow Builder) & cải tiến backend tùy chọn

1. **Workflow Builder UI (W8):** Tạo mới và chỉnh sửa cấu trúc quy trình (`POST /api/workflows`, `PATCH /api/workflows/{id}`), cấu hình `stages`, `stage_members`, `sla_hours`, `assign_mode`, `form_fields`, `roles`, `failure_reasons`, và đổi trạng thái `active/closed`. W7 hiện chỉ xem danh sách/thành viên và thao tác trên quy trình đang có.
2. **Mở rộng backend tùy chọn (không chặn W7):**
   - `GET /api/workflows` hiện chưa trả sẵn số lượng task/hoàn thành/thất bại/quá hạn, nên W7 đang gọi `GET /api/workflows/{id}/board?per_column=1` song song; W8 hoặc bản tối ưu sau có thể gộp count trực tiếp vào `/api/workflows`.
   - `GET /api/jobs` hỗ trợ `role`, `status`, `workflow_id`, `stage_id`, `q` và 3 khoảng ngày; bộ lọc `creator_id` / `assignee_id` trong `FilterDrawer` hiện lọc bổ sung trên trang kết quả phía client nếu cần có thể thêm query param trên server sau.
   - `TaskCreateIn` tính deadline bước 1 tự động theo `sla_hours` (hiện tại "Hạn mong muốn" được ghi kèm mô tả nếu cần ghi chú thêm); nhiều tệp đính kèm có thể lưu bảng riêng trong tương lai bên cạnh 1 `tasks.document_id` chính dùng cho luồng ký W6.

---

## 4. Kiểm thử & Build

```bash
# Unit test & production build frontend
cd frontend
npm install
npm test
npm run build

# Test bảo mật tĩnh & Playwright E2E W7 (tự động chạy local SPA nếu chưa đặt WEB_LIVE_URL)
pytest backend/tests/test_e2e_playwright.py::test_frontend_static_security_checks backend/tests/test_w7_wfengine_playwright.py -v

# Toàn bộ bộ test Docker Compose cô lập
docker compose -f docker-compose.test.yml --profile test run --rm tests
```

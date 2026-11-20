# W6 — Tích hợp bước ký & duyệt vào Workflow Engine (Backend)

Nối động cơ quy trình **W5** (`Workflow → Stage → Task → Todo`) với luồng ký số nội bộ **W1–W4** (`Document → WorkflowStep → StepAssignee`, pyHanko, PIN 6 số, kiểm toán `audit_logs`, thông báo), không làm thay đổi API/bảng W1–W5 và **không sửa frontend**.

---

## 1. Schema & Migration idempotent ([models.py](backend/app/models.py))

Hàm `init_db()` / `migrate_db()` bổ sung cột và chỉ mục an toàn (chạy lại nhiều lần không lỗi trên DB W1–W5):

- **`workflow_steps.task_id`**: `INTEGER NULL REFERENCES tasks(id) ON DELETE SET NULL`, kèm index `ix_workflow_steps_task_id`.
- **`todos.workflow_step_id`**: `INTEGER NULL REFERENCES workflow_steps(id) ON DELETE SET NULL`, kèm index `ix_todos_workflow_step_id`.
- **`todos.step_assignee_id`**: `INTEGER NULL REFERENCES step_assignees(id) ON DELETE SET NULL`, kèm index `ix_todos_step_assignee_id`.

---

## 2. Hook tích hợp & Điểm nối ([wf_signing.py](backend/app/wf_signing.py), [wf_hooks.py](backend/app/wf_hooks.py))

Hàm `register_w6_handlers()` được gọi trong `create_app()` ([main.py](backend/app/main.py)) để đăng ký handler vào engine [wf_engine.py](backend/app/wf_engine.py):

1. **`SignStageHandler` (`stage.type == "sign"`):**
   - `on_enter(ctx, todos)`:
     - Chặn `409` nếu `task.document_id` chưa được gắn hoặc tài liệu/file không hợp lệ.
     - Khóa phiên bản hiện tại (tạo `DocumentVersion` v0 nếu chưa có) và đánh dấu `is_workflow=True`.
     - Gọi lại `_create_step(...)` của W2 ([routes_workflow.py](backend/app/routes_workflow.py)) với `step.task_id = task.id`, `mode = stage.assign_mode`, `assignees = thành viên được giao todo`.
     - Liên kết ngược `todo.workflow_step_id = step.id` và `todo.step_assignee_id = sa.id`.
     - Ghi `audit_logs` hành động `doc_assign` kèm `task_id`, `workflow_id`, `document_id`, `locked_version_no`, và gửi thông báo giao ký (`task_assigned` kèm `document_id` & `task_id`).
   - `before_complete(ctx, todo, payload)`:
     - Chặn `409` mọi yêu cầu hoàn thành thủ công `POST /api/todos/{id}/complete` trên bước `sign` (bắt buộc đi qua luồng ký có `_signed_verified=True` và `StepAssignee.status == "signed"`).
2. **Hoàn tất ký số → tự đóng todo & chuyển stage ([routes_workflow.py](backend/app/routes_workflow.py)):**
   - Khi ký ở bước cuối / đủ chữ ký (`assign_mode="any"` hoặc tất cả đã ký với `assign_mode="all"`), engine đóng bước ký (`WorkflowStep.status = "completed"`), ghi JSON tham chiếu chữ ký (`signer`, `display_name`, `signed_at`, `version_no`, `file_id`, `sign_job_id`, `cert_key_ref`) vào `todo.result`, gọi `complete_todo(..., _signed_verified=True)` để tiến sang stage tiếp theo hoặc chốt `completed`.
   - Khi task hoàn thành stage cuối, `complete_linked_document_if_done()` chốt `Document.status = "completed"`, `completed_at`, và ghi `doc_complete` audit.
3. **`ApproveStageHandler` (`stage.type == "approve"`):**
   - `POST /api/todos/{id}/complete` nhận `decision = "approve" | "reject"` (mặc định `"approve"` nếu bỏ trống; không dùng chứng thư hay PIN).
   - `approve`: sang stage kế tiếp; `reject`: bắt buộc có `failure_reason_id` thuộc quy trình và chuyển qua `fail_todo(..., decision="reject")`; cả hai đều ghi `decision` vào metadata của `audit_logs`.

---

## 3. API W6 (`/api/...`)

| Method & Path | Mô tả | Ghi chú quyền & luồng |
| --- | --- | --- |
| `POST /api/tasks/{id}/document` | Gắn tài liệu vào task: multipart upload (PDF hoặc Office qua `ingest_uploaded_file` dùng chung W1) hoặc JSON `{file_id}` / `{document_id}` | Chỉ `creator`, `admin` quy trình hoặc assignee đang `doing`; chặn `409` nếu bước `sign` đã bắt đầu |
| `GET /api/tasks/{id}/document/file` | Tải PDF hiện tại (hoặc `?version_no=`) của tài liệu gắn task | Kiểm tra visibility W5 qua `get_visible_task`, ghi audit `download` |
| `POST /api/tasks/{id}/document/verify` | Xác minh chữ ký PDF qua `signer_verify` | Kiểm tra visibility W5, rate-limit `verify`, ghi audit |
| `POST /api/tasks/{id}/sign` | Ký số trực tiếp theo `task_id` (gọi lại `sign_document`) | Kiểm tra PIN, `StageMember`, cert, gọi pyHanko (`signer_sign`), tự hoàn thành todo |
| `POST /api/tasks/{id}/reject` | Từ chối ký theo `task_id` (gọi lại `reject_document`) | Bắt buộc PIN + `failure_reason_id` thuộc quy trình → `task.status="failed"`, hủy todo mở |
| `POST /api/tasks/{id}/cancel` | Hủy task đang `processing` | Hủy mọi todo mở + gọi `cancel_open_sign_steps_for_task()` (ghi audit `doc_cancel` & `task_cancelled`) |
| `POST /api/todos/{id}/fail` | Nếu todo thuộc stage `sign`, chuyển tiếp sang `reject_document` | Vẫn kiểm tra `verify_signing_pin`, không tạo đường vòng qua PIN |
| `GET /api/tasks/{id}` | Bổ sung `document` (`id`, `name`, `current_version_no`, `versions`, `signatures`, `current_sign_step`) và hợp nhất `activity` (task + document) | Timeline `/api/tasks/{id}/timeline` cũng hợp nhất `task_id` & `document_id` |

---

## 4. Ràng buộc bảo mật & Đồng thời

- **Thứ tự khóa chuẩn (`task_lock(task_id)` → `_doc_lock(doc_id)`):** Tránh deadlock khi thao tác đồng thời qua `/api/tasks/...` và `/api/documents/...`.
- **Kiểm tra `StageMember` ở cả hai đầu:** Cả `wf_engine` lẫn `routes_workflow` (`sign_document` / `reject_document`) đều kiểm tra người ký/từ chối phải nằm trong `stage_members` của stage hiện tại và có todo đang `doing`.
- **Khóa tài liệu ngoài luồng W6:** Tài liệu đã gắn vào task bị chặn xóa file (`DELETE /api/files/{id}` → `409`), ký lẻ (`POST /api/sign` → `409`), và bị chặn `POST /api/documents/{id}/assign`, `/complete`, `/cancel` hoặc truyền `next_action` khi ký (`409`).
- **Visibility kế thừa W5:** `_can_view_document()` và `GET /api/documents` áp dụng `visible_task_clause` đối với tài liệu gắn task — người không thấy task (`followers_only`) nhận `404` ở mọi endpoint tài liệu.
- **Bảo toàn PIN & rate-limit:** Mọi hành động ký/từ chối qua task hoặc todo bước `sign` đều dùng `verify_signing_pin(..., task_id=..., workflow_id=...)`; sai 5 lần trả `423` (`X-Pin-Error: pin_locked`) và không tiến bước.
- **Hủy / Lưu trữ task:** `POST /api/tasks/{id}/cancel` và `POST /api/tasks/{id}/archive` tự động gọi `cancel_open_sign_steps_for_task()` để hủy `WorkflowStep` còn `active` và ghi audit `doc_cancel`.

---

## 5. Chạy kiểm thử

### Chạy đầy đủ trong môi trường Docker Compose test cô lập (gồm `signer`, `tsa`, `converter`, `ca`, Playwright):
```bash
docker compose -f docker-compose.test.yml --profile test run --rm tests
```

### Chạy riêng bộ test W6 & hồi quy backend (chế độ cục bộ khi không có Docker services):
```bash
pytest backend/tests/test_w6_signing.py backend/tests/test_wfengine.py -v
pytest backend/tests --ignore=backend/tests/test_files_sign.py --ignore=backend/tests/test_workflow_playwright.py -k "not live and not playwright_e2e and not caddy_https and not expired_crl and not expiry_warnings and not encrypted_backup" -v
```

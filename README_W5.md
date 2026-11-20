# W5 — Workflow Engine (Backend)

Bổ sung động cơ quy trình tổng quát kiểu **Workflow → Stage → Task → Todo**, chạy song song và không làm thay đổi API/bảng của luồng ký hiện có (W1–W4). Mọi hành động trên quy trình và công việc đều ghi vào `audit_logs` (chỉ thêm) và kiểm tra quyền/visibility ở tầng server.

---

## 1. Schema & Migration ([models.py](backend/app/models.py))

Khi ứng dụng khởi chạy (`init_db` / `migrate_db`), các bảng mới, chỉ mục và cột bổ sung được tạo tự động (idempotent, an toàn trên DB đã có dữ liệu W1–W4):

- **Bảng mới:**
  - `workflows` (`id, name, slug, category, description, guide_html, visibility, status, creator_id, created_at`)
  - `workflow_roles` (`workflow_id, user_id, role` ∈ `admin|supervisor|creator|viewer`)
  - `workflow_failure_reasons` (`id, workflow_id, label, order`)
  - `stages` (`id, workflow_id, order, name, type, sla_hours, guide_html, assign_mode`)
  - `stage_members` (`stage_id, user_id`)
  - `form_fields` (`id, workflow_id, label, type, required, placeholder, options, order`)
  - `tasks` (`id, workflow_id, current_stage_id, title, labels [text[] / JSON], creator_id, description_html, custom_values, deadline, status, failure_reason_id, document_id, created_at, updated_at, completed_at, archived_at`)
  - `task_followers` (`task_id, user_id`)
  - `todos` (`id, task_id, stage_id, assignee_id, result, deadline, status, created_at, completed_at`)
- **Cột mở rộng trên bảng có sẵn:**
  - `notifications.task_id` (FK `tasks.id ON DELETE CASCADE`)
  - `audit_logs.task_id`, `audit_logs.workflow_id`
- **Index:** `ix_tasks_workflow_status`, `ix_tasks_current_stage`, `ix_todos_assignee_status`, `ix_todos_deadline`.
- **Trạng thái quá hạn (suy ra, không lưu cứng):** `is_overdue = (deadline < now AND status == 'processing')`.

---

## 2. Danh sách API mới (`/api/...`)

Mọi endpoint đều yêu cầu đã đăng nhập, qua 2FA và hoàn tất onboarding (`require_mfa`):

| Method & Path | Mô tả | Quyền |
| --- | --- | --- |
| `POST /api/workflows` | Tạo quy trình kèm `stages`, `form_fields`, `roles`, `failure_reasons` | Quản trị hệ thống |
| `GET /api/workflows` | Danh sách quy trình được tham gia/xem | User có role hoặc thuộc `stage_members` / system admin |
| `GET /api/workflows/{id}` | Chi tiết quy trình | User xem được quy trình |
| `PATCH /api/workflows/{id}` | Sửa cấu trúc quy trình (`name`, `stages`, `form_fields`, `roles`, `failure_reasons`, `visibility`, `status`) | Admin của quy trình / system admin (`supervisor` chỉ đọc) |
| `POST /api/workflows/{id}/tasks` | Tạo công việc (validate form động `form_fields`, sinh todo bước 1 cho `stage_members`) | `admin` / `creator` của quy trình |
| `GET /api/workflows/{id}/board` | Bảng Kanban theo từng stage (`tasks`, `count`, `overdue_count`) + 2 cột `completed`, `failed` | Lọc theo `visibility` ở server |
| `GET /api/jobs` | Tìm kiếm & lọc công việc (`role=assigned\|created\|following\|team`, `status=processing\|completed\|failed\|overdue`, `workflow_id`, `stage_id`, `q`, `created_from/to`, `deadline_from/to`, `completed_from/to`, `archived`, `sort`, `page`, `page_size`) | Lọc theo `visibility` ở server |
| `GET /api/jobs/export` | Xuất CSV theo đúng bộ lọc và `visibility` của `/api/jobs` | Lọc theo `visibility` ở server |
| `GET /api/todos` | Danh sách việc cần làm (`role=assigned\|created\|following\|team`, `status=doing\|done\|failed\|cancelled\|overdue`) | Lọc theo `visibility` ở server |
| `POST /api/todos/{id}/complete` | Hoàn thành todo (`{result, next_assignee_ids?}`); tự chuyển stage kế tiếp theo `assign_mode` (`any`/`all`) hoặc chốt `completed` | Assignee thuộc `stage_members` |
| `POST /api/todos/{id}/fail` | Báo thất bại (`{failure_reason_id, note?}`); bắt buộc lý do thuộc workflow, hủy toàn bộ todo đang mở | Assignee thuộc `stage_members` |
| `GET /api/tasks/{id}` | Chi tiết công việc, form `custom_values`, `todos` và `activity` | Theo `visibility` của task |
| `PATCH /api/tasks/{id}` | Sửa tiêu đề, nhãn, mô tả HTML (đã sanitize), `custom_values` | Người tạo hoặc admin quy trình |
| `POST /api/tasks/{id}/followers` | Thêm người theo dõi (`{user_id}`) | Người tạo hoặc admin quy trình |
| `DELETE /api/tasks/{id}/followers/{user_id}` | Gỡ người theo dõi | Người tạo hoặc admin quy trình |
| `POST /api/tasks/{id}/archive` | Lưu trữ công việc (chỉ khi đã `completed`/`failed`) | Người tạo hoặc admin quy trình |
| `POST /api/tasks/{id}/restore` | Khôi phục công việc đã lưu trữ | Người tạo hoặc admin quy trình |
| `GET /api/tasks/{id}/timeline` | Lịch sử hành trình dùng chung định dạng sự kiện với `/api/documents/{id}/timeline` | Theo `visibility` của task |

---

## 3. Seed 2 quy trình mẫu ([seed_workflows.py](backend/app/seed_workflows.py))

Chạy idempotent (theo `slug`) trong container `api`:
```bash
docker compose exec api python3 -m app.seed_workflows
```
Tạo 2 quy trình mẫu:
1. **`QT công văn đến`** (`slug=qt-cong-van-den`, `visibility=followers_only`): 5 bước (`Văn thư tiếp nhận` 8h → `Giám đốc cho ý kiến` 2h [`approve`] → `Bộ môn thực hiện` 24h [`all`] → `Giám đốc ký duyệt` 1h [`sign`] → `Văn thư phát hành` 1h), 5 trường bắt buộc (`Tên công văn`, `Đơn vị gửi`, `Trích yếu`, `Thời hạn trả lời` [`date`], `Scan văn bản` [`file`]).
2. **`QT Chấm công`** (`slug=qt-cham-cong`, `visibility=all_visible`): 3 bước (1h → 3h → 4h), không có form riêng.

---

## 4. Hook tích hợp bước ký (W6) ([wf_hooks.py](backend/app/wf_hooks.py))

Ở W5, các bước `stage.type` ∈ `{normal, sign, approve}` dùng `StageHandler` mặc định (xử lý tuần tự như `normal`). Bảng `tasks` có sẵn cột nullable `document_id` (FK `documents.id`) và `GET /api/tasks/{id}/timeline` tự hợp nhất event từ cả `task_id` và `document_id`.

Khi triển khai W6, chỉ cần implement subclass của `StageHandler` và đăng ký lúc khởi động ứng dụng trong giao dịch của engine:

```python
from app.wf_hooks import StageContext, StageHandler, register_stage_handler

class SignStageHandler(StageHandler):
    def on_enter(self, ctx: StageContext, todos: list) -> None:
        # Tạo hoặc gắn Document + WorkflowStep (W2) vào ctx.task.document_id
        ...

    def before_complete(self, ctx: StageContext, todo, payload: dict) -> None:
        # Từ chối hoàn thành todo nếu chữ ký tương ứng trên Document chưa hoàn tất
        ...

    def after_complete(self, ctx: StageContext, todo, payload: dict) -> None:
        # Đồng bộ trạng thái/phiên bản file sau ký
        ...

register_stage_handler("sign", SignStageHandler())
```

> Lưu ý: Handler chạy **bên trong** SQLAlchemy session `ctx.db` của engine (`Task` đã được khóa), nên không tự gọi `commit()` và chỉ ném `HTTPException` khi cần hủy thao tác.

"""Hook (điểm cắm) cho từng loại stage của workflow engine (W5).

W5 xử lý mọi stage như `normal`. Bước ký (W6) cắm vào bằng cách đăng ký handler riêng:

    from app.wf_hooks import StageHandler, register_stage_handler

    class SignStageHandler(StageHandler):
        def on_enter(self, ctx, todos):          # tạo/gắn luồng ký (Document) cho task
            ...
        def before_complete(self, ctx, todo, payload):   # chặn hoàn thành todo nếu chưa ký xong
            if not signed: raise HTTPException(409, "Cần ký trước khi hoàn thành")
        def after_complete(self, ctx, todo, payload):    # đồng bộ kết quả ký
            ...

    register_stage_handler("sign", SignStageHandler())
    register_stage_handler("approve", ...)

Handler chạy BÊN TRONG giao dịch của engine (cùng session `ctx.db`, chưa commit): ném HTTPException
để hủy toàn bộ thao tác; không được tự commit. Task có sẵn cột `document_id` để liên kết với luồng ký.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any, Dict, List, Optional


@dataclass
class StageContext:
    db: Any                 # SQLAlchemy Session (đang trong giao dịch của request)
    settings: Any
    task: Any               # models.Task
    stage: Any              # models.Stage đang xử lý
    actor: Optional[Any]    # models.User thực hiện hành động (None khi hệ thống)
    now: datetime           # thời điểm logic của engine (đồng hồ chung, giả lập được trong test)
    ip: str = ""


class StageHandler:
    """Handler mặc định (normal): không làm gì thêm."""

    def on_enter(self, ctx: StageContext, todos: List[Any]) -> None:
        """Gọi sau khi task vào stage và các todo đã được sinh (đã flush)."""

    def before_complete(self, ctx: StageContext, todo: Any, payload: Dict[str, Any]) -> None:
        """Gọi trước khi đánh dấu todo hoàn thành; ném HTTPException để từ chối."""

    def after_complete(self, ctx: StageContext, todo: Any, payload: Dict[str, Any]) -> None:
        """Gọi sau khi todo đã được đánh dấu hoàn thành (trước khi quyết định chuyển stage)."""


_DEFAULT = StageHandler()
_HANDLERS: Dict[str, StageHandler] = {"normal": _DEFAULT, "sign": _DEFAULT, "approve": _DEFAULT}


def register_stage_handler(stage_type: str, handler: StageHandler) -> None:
    if stage_type not in _HANDLERS:
        raise ValueError(f"stage type không hợp lệ: {stage_type}")
    _HANDLERS[stage_type] = handler


def get_stage_handler(stage_type: str) -> StageHandler:
    return _HANDLERS.get(stage_type, _DEFAULT)


def reset_stage_handlers() -> None:
    """Khôi phục handler mặc định (dùng trong test)."""
    for k in list(_HANDLERS):
        _HANDLERS[k] = _DEFAULT

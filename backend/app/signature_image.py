"""Xử lý ảnh chữ ký nội bộ bằng Pillow: xóa EXIF, xóa nền thành trong suốt, xoay và tự cắt sát nét chữ."""
from __future__ import annotations

import io
from statistics import median

from fastapi import HTTPException
from PIL import Image, ImageChops, ImageOps


def _estimate_bg_color(im: Image.Image) -> tuple[int, int, int]:
    w, h = im.size
    step_x = max(1, w // 40)
    step_y = max(1, h // 40)
    samples: list[tuple[int, int, int]] = []
    px = im.load()
    for x in range(0, w, step_x):
        for y in (0, h - 1):
            r, g, b, a = px[x, y]
            if a >= 16:
                samples.append((r, g, b))
    for y in range(0, h, step_y):
        for x in (0, w - 1):
            r, g, b, a = px[x, y]
            if a >= 16:
                samples.append((r, g, b))
    if not samples:
        return (255, 255, 255)
    br = int(median(c[0] for c in samples))
    bg = int(median(c[1] for c in samples))
    bb = int(median(c[2] for c in samples))
    luma = 0.299 * br + 0.587 * bg + 0.114 * bb
    if luma < 128:
        return (255, 255, 255)
    return (br, bg, bb)


def process_signature_image(
    data: bytes,
    max_bytes: int,
    *,
    remove_bg: bool = True,
    sensitivity: int = 55,
    rotate: int = 0,
) -> tuple[bytes, int, int]:
    """Kiểm tra thật sự là PNG/JPEG, chuẩn hóa hướng, xóa EXIF, xóa nền (tùy chọn) và cắt gọn."""
    if not data:
        raise HTTPException(400, "File ảnh rỗng")
    if len(data) > max_bytes:
        raise HTTPException(413, "Ảnh chữ ký quá lớn")
    if rotate not in (0, 90, 180, 270):
        raise HTTPException(400, "Góc xoay chỉ hỗ trợ 0, 90, 180 hoặc 270 độ")

    try:
        probe = Image.open(io.BytesIO(data))
        fmt = (probe.format or "").upper()
        if fmt not in ("PNG", "JPEG"):
            raise HTTPException(400, "Ảnh chữ ký chỉ nhận PNG/JPEG")
        pw, ph = probe.size
        if pw < 8 or ph < 8 or pw > 4096 or ph > 4096 or pw * ph > 12_000_000:
            raise HTTPException(400, "Kích thước ảnh không hợp lệ (8..4096px)")
        probe.verify()
    except HTTPException:
        raise
    except Exception:
        raise HTTPException(400, "Ảnh chữ ký không hợp lệ hoặc bị hỏng")

    try:
        src = Image.open(io.BytesIO(data))
        src.load()
        src = ImageOps.exif_transpose(src)
        im = src.convert("RGBA")
    except Exception:
        raise HTTPException(400, "Không đọc được dữ liệu ảnh chữ ký")

    if max(im.size) > 1400:
        im.thumbnail((1400, 1400), Image.Resampling.LANCZOS)

    if rotate:
        im = im.rotate(-rotate, expand=True)

    if remove_bg:
        sens = max(0, min(100, int(sensitivity)))
        br, bg, bb = _estimate_bg_color(im)
        rgb = im.convert("RGB")
        bg_img = Image.new("RGB", rgb.size, (br, bg, bb))
        diff = ImageChops.difference(rgb, bg_img)
        dr, dg, db_ch = diff.split()
        dist = ImageChops.lighter(ImageChops.lighter(dr, dg), db_ch)

        t_low = int(15 + sens * 0.7)
        t_high = min(240, t_low + 36)
        span = max(1, t_high - t_low)
        lut = [
            0 if d <= t_low else (255 if d >= t_high else int((d - t_low) * 255 / span))
            for d in range(256)
        ]
        mask = dist.point(lut)
        orig_alpha = im.getchannel("A")
        new_alpha = ImageChops.darker(orig_alpha, mask)
        im.putalpha(new_alpha)

        bbox = new_alpha.point(lambda p: 255 if p > 8 else 0).getbbox()
        if bbox is None:
            raise HTTPException(
                400,
                "Không phát hiện nét chữ ký sau khi xóa nền (hãy giảm độ nhạy hoặc tắt xóa nền)",
            )
        pad = 8
        im = im.crop((
            max(0, bbox[0] - pad),
            max(0, bbox[1] - pad),
            min(im.width, bbox[2] + pad),
            min(im.height, bbox[3] + pad),
        ))
    else:
        alpha = im.getchannel("A")
        bbox = alpha.point(lambda p: 255 if p > 8 else 0).getbbox()
        if bbox is None:
            raise HTTPException(400, "Ảnh chữ ký trong suốt hoàn toàn")
        pad = 8
        im = im.crop((
            max(0, bbox[0] - pad),
            max(0, bbox[1] - pad),
            min(im.width, bbox[2] + pad),
            min(im.height, bbox[3] + pad),
        ))

    if im.width > 900 or im.height > 450:
        im.thumbnail((900, 450), Image.Resampling.LANCZOS)
    if im.width < 4 or im.height < 4:
        raise HTTPException(400, "Vùng chữ ký quá nhỏ sau khi cắt")

    out = io.BytesIO()
    im.save(out, format="PNG", optimize=True)
    return out.getvalue(), im.width, im.height

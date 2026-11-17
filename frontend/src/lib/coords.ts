export interface PixelRect {
  x: number;
  y: number;
  w: number;
  h: number;
}

export type PdfBox = [number, number, number, number]; // [x1, y1, x2, y2] theo PDF user space (gốc dưới-trái)

export interface ViewportLike {
  width: number;
  height: number;
  convertToPdfPoint(x: number, y: number): number[];
  convertToViewportPoint(x: number, y: number): number[];
}

const r2 = (n: number): number => Math.round(n * 100) / 100;

/** Giới hạn khung pixel nằm gọn bên trong trang và đạt kích thước tối thiểu. */
export function clampPixelRect(
  rect: PixelRect,
  pageW: number,
  pageH: number,
  minPx = 24,
): PixelRect {
  const maxW = Math.max(minPx, pageW);
  const maxH = Math.max(minPx, pageH);
  const w = Math.min(Math.max(rect.w, minPx), maxW);
  const h = Math.min(Math.max(rect.h, minPx), maxH);
  const x = Math.min(Math.max(rect.x, 0), Math.max(0, pageW - w));
  const y = Math.min(Math.max(rect.y, 0), Math.max(0, pageH - h));
  return { x, y, w, h };
}

/**
 * Quy đổi khung pixel (gốc trên-trái của pdf.js viewport) sang hộp tọa độ PDF [x1, y1, x2, y2]
 * theo user space của trang (xử lý tự động scale, CropBox/MediaBox lệch gốc và /Rotate 0/90/180/270).
 */
export function pixelRectToPdfBox(rect: PixelRect, viewport: ViewportLike): PdfBox {
  const p1 = viewport.convertToPdfPoint(rect.x, rect.y);
  const p2 = viewport.convertToPdfPoint(rect.x + rect.w, rect.y + rect.h);
  return [
    r2(Math.min(p1[0], p2[0])),
    r2(Math.min(p1[1], p2[1])),
    r2(Math.max(p1[0], p2[0])),
    r2(Math.max(p1[1], p2[1])),
  ];
}

/** Quy đổi hộp tọa độ PDF [x1, y1, x2, y2] sang khung pixel trên viewport hiện tại. */
export function pdfBoxToPixelRect(box: PdfBox, viewport: ViewportLike): PixelRect {
  const c1 = viewport.convertToViewportPoint(box[0], box[1]);
  const c2 = viewport.convertToViewportPoint(box[2], box[3]);
  return {
    x: Math.min(c1[0], c2[0]),
    y: Math.min(c1[1], c2[1]),
    w: Math.abs(c2[0] - c1[0]),
    h: Math.abs(c2[1] - c1[1]),
  };
}

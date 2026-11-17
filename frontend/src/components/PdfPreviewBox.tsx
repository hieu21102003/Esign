import React, { useEffect, useRef, useState } from "react";
import * as pdfjsLib from "pdfjs-dist";
import workerUrl from "pdfjs-dist/build/pdf.worker.min.mjs?url";
import { clampPixelRect, pdfBoxToPixelRect, pixelRectToPdfBox, type PdfBox, type PixelRect, type ViewportLike } from "../lib/coords";

pdfjsLib.GlobalWorkerOptions.workerSrc = workerUrl;

interface Props {
  pdfBytes: Uint8Array | null;
  page: number;
  onPageChange: (page: number) => void;
  box: PdfBox;
  onBoxChange: (box: PdfBox) => void;
  mode: "text" | "image" | "both";
  reason: string;
  signerName: string;
  imagePreviewUrl: string | null;
}

export function PdfPreviewBox({
  pdfBytes,
  page,
  onPageChange,
  box,
  onBoxChange,
  mode,
  reason,
  signerName,
  imagePreviewUrl,
}: Props) {
  const canvasRef = useRef<HTMLCanvasElement | null>(null);
  const [doc, setDoc] = useState<pdfjsLib.PDFDocumentProxy | null>(null);
  const [numPages, setNumPages] = useState(0);
  const [scale, setScale] = useState(1.0);
  const [vp, setVp] = useState<ViewportLike | null>(null);
  const [pageMeta, setPageMeta] = useState<{ w: number; h: number; rotate: number }>({ w: 0, h: 0, rotate: 0 });
  const [rectPx, setRectPx] = useState<PixelRect>({ x: 40, y: 40, w: 240, h: 80 });
  const [loadErr, setLoadErr] = useState("");
  const dragRef = useRef<{
    kind: "move" | "resize";
    startX: number;
    startY: number;
    orig: PixelRect;
  } | null>(null);

  // Nạp tài liệu PDF bằng pdf.js (worker cục bộ)
  useEffect(() => {
    let cancelled = false;
    setLoadErr("");
    if (!pdfBytes) {
      setDoc(null);
      setNumPages(0);
      setVp(null);
      return;
    }
    const task = pdfjsLib.getDocument({ data: pdfBytes.slice() });
    task.promise
      .then((loaded) => {
        if (cancelled) {
          loaded.destroy();
          return;
        }
        setDoc(loaded);
        setNumPages(loaded.numPages);
      })
      .catch(() => {
        if (!cancelled) setLoadErr("Không thể hiển thị PDF");
      });
    return () => {
      cancelled = true;
      task.destroy();
    };
  }, [pdfBytes]);

  // Render trang hiện tại lên canvas & đồng bộ khung chữ ký
  useEffect(() => {
    let cancelled = false;
    let renderTask: pdfjsLib.RenderTask | null = null;
    if (!doc || page < 1 || page > doc.numPages) return;

    doc.getPage(page).then((pdfPage) => {
      if (cancelled) return;
      const viewport = pdfPage.getViewport({ scale });
      const canvas = canvasRef.current;
      if (!canvas) return;
      const ctx = canvas.getContext("2d");
      if (!ctx) return;

      canvas.width = Math.round(viewport.width);
      canvas.height = Math.round(viewport.height);
      setVp(viewport);
      setPageMeta({
        w: Math.round(viewport.width / scale),
        h: Math.round(viewport.height / scale),
        rotate: pdfPage.rotate,
      });

      const nextRect = clampPixelRect(
        pdfBoxToPixelRect(box, viewport),
        viewport.width,
        viewport.height,
      );
      setRectPx(nextRect);
      const clampedBox = pixelRectToPdfBox(nextRect, viewport);
      if (clampedBox.some((v, idx) => Math.abs(v - box[idx]) > 0.5)) {
        onBoxChange(clampedBox);
      }

      renderTask = pdfPage.render({ canvasContext: ctx, viewport });
      return renderTask.promise;
    }).catch(() => {
      // ignore cancelled render
    });

    return () => {
      cancelled = true;
      renderTask?.cancel();
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [doc, page, scale]);

  // Khi box thay đổi từ bên ngoài (ví dụ chọn preset hoặc reset) -> cập nhật rectPx
  useEffect(() => {
    if (!vp || dragRef.current) return;
    setRectPx(clampPixelRect(pdfBoxToPixelRect(box, vp), vp.width, vp.height));
  }, [box, vp]);

  function onPointerDown(kind: "move" | "resize", e: React.PointerEvent) {
    e.preventDefault();
    e.stopPropagation();
    (e.currentTarget as HTMLElement).setPointerCapture(e.pointerId);
    dragRef.current = { kind, startX: e.clientX, startY: e.clientY, orig: rectPx };
  }

  function onPointerMove(e: React.PointerEvent) {
    const d = dragRef.current;
    if (!d || !vp) return;
    const dx = e.clientX - d.startX;
    const dy = e.clientY - d.startY;
    let next: PixelRect;
    if (d.kind === "move") {
      next = clampPixelRect({ ...d.orig, x: d.orig.x + dx, y: d.orig.y + dy }, vp.width, vp.height);
    } else {
      const maxW = vp.width - d.orig.x;
      const maxH = vp.height - d.orig.y;
      next = {
        x: d.orig.x,
        y: d.orig.y,
        w: Math.min(Math.max(d.orig.w + dx, 24), maxW),
        h: Math.min(Math.max(d.orig.h + dy, 24), maxH),
      };
    }
    setRectPx(next);
    onBoxChange(pixelRectToPdfBox(next, vp));
  }

  function onPointerUp(e: React.PointerEvent) {
    if (!dragRef.current) return;
    try {
      (e.currentTarget as HTMLElement).releasePointerCapture(e.pointerId);
    } catch {
      // ignore
    }
    dragRef.current = null;
  }

  return (
    <div className="preview-panel">
      <div className="preview-toolbar">
        <div className="row">
          <button
            type="button"
            className="btn small"
            disabled={!doc || page <= 1}
            onClick={() => onPageChange(page - 1)}
          >
            Trang trước
          </button>
          <span className="muted" data-testid="page-indicator">
            Trang {numPages ? page : 0} / {numPages}
          </span>
          <button
            type="button"
            className="btn small"
            disabled={!doc || page >= numPages}
            onClick={() => onPageChange(page + 1)}
          >
            Trang sau
          </button>
        </div>
        <div className="row">
          <label className="inline-label">
            Thu phóng
            <select value={scale} onChange={(e) => setScale(Number(e.target.value))}>
              <option value={0.75}>75%</option>
              <option value={1}>100%</option>
              <option value={1.25}>125%</option>
              <option value={1.5}>150%</option>
            </select>
          </label>
          {vp && (
            <span className="muted mono" data-testid="page-meta">
              {pageMeta.w}x{pageMeta.h}pt · Xoay {pageMeta.rotate}°
            </span>
          )}
        </div>
      </div>

      {loadErr && <div className="alert error">{loadErr}</div>}

      <div className="canvas-scroll">
        {!pdfBytes ? (
          <div className="empty-preview">Tải lên tài liệu Word hoặc PDF để xem trước và đặt vị trí chữ ký</div>
        ) : (
          <div
            className="pdf-stage"
            data-testid="pdf-stage"
            style={{ width: vp ? vp.width : undefined, height: vp ? vp.height : undefined }}
          >
            <canvas ref={canvasRef} className="pdf-canvas" data-testid="pdf-canvas" />
            {vp && (
              <div
                className="sig-box"
                data-testid="sig-box"
                style={{ left: rectPx.x, top: rectPx.y, width: rectPx.w, height: rectPx.h }}
                onPointerDown={(e) => onPointerDown("move", e)}
                onPointerMove={onPointerMove}
                onPointerUp={onPointerUp}
              >
                <div className="sig-box-inner">
                  {(mode === "image" || mode === "both") && (
                    <div className={mode === "both" ? "sig-img-side" : "sig-img-only"}>
                      {imagePreviewUrl ? (
                        <img src={imagePreviewUrl} alt="Ảnh chữ ký" draggable={false} />
                      ) : (
                        <span className="muted">Ảnh chữ ký</span>
                      )}
                    </div>
                  )}
                  {(mode === "text" || mode === "both") && (
                    <div className="sig-text-side">
                      <div><strong>Ký bởi:</strong> {signerName}</div>
                      {reason && <div className="sig-reason-line">Nhận xét: {reason}</div>}
                    </div>
                  )}
                </div>
                <div
                  className="sig-resize-handle"
                  data-testid="sig-resize"
                  onPointerDown={(e) => onPointerDown("resize", e)}
                  onPointerMove={onPointerMove}
                  onPointerUp={onPointerUp}
                />
              </div>
            )}
          </div>
        )}
      </div>

      {vp && (
        <div className="coords-bar mono" data-testid="coords-bar">
          Tọa độ PDF (pt): [{box.map((v) => v.toFixed(1)).join(", ")}]
        </div>
      )}
    </div>
  );
}

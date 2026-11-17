import { describe, expect, it } from "vitest";
import * as pdfjsLib from "pdfjs-dist/legacy/build/pdf.mjs";
import { pdfBoxToPixelRect, pixelRectToPdfBox } from "./coords";

function minimalPdf(mediaBox: [number, number, number, number], cropBox: [number, number, number, number], rotate: number): Uint8Array {
  const objs = [
    "1 0 obj\n<< /Type /Catalog /Pages 2 0 R >>\nendobj\n",
    "2 0 obj\n<< /Type /Pages /Kids [3 0 R] /Count 1 >>\nendobj\n",
    `3 0 obj\n<< /Type /Page /Parent 2 0 R /MediaBox [${mediaBox.join(" ")}] /CropBox [${cropBox.join(" ")}] /Rotate ${rotate} >>\nendobj\n`,
  ];
  let out = "%PDF-1.4\n";
  const offs: number[] = [];
  for (const o of objs) {
    offs.push(out.length);
    out += o;
  }
  const xref = out.length;
  out += `xref\n0 ${objs.length + 1}\n0000000000 65535 f \n`;
  for (const off of offs) {
    out += `${String(off).padStart(10, "0")} 00000 n \n`;
  }
  out += `trailer\n<< /Size ${objs.length + 1} /Root 1 0 R >>\nstartxref\n${xref}\n%%EOF\n`;
  return new TextEncoder().encode(out);
}

describe("coords conversion with pdfjs PageViewport", () => {
  it("handles A4 normal (rotate=0, origin=[0,0])", async () => {
    const doc = await pdfjsLib.getDocument({ data: minimalPdf([0, 0, 595, 842], [0, 0, 595, 842], 0), verbosity: 0 }).promise;
    const page = await doc.getPage(1);
    const vp = page.getViewport({ scale: 1.5 });
    const rect = { x: 60, y: 120, w: 300, h: 120 };
    const box = pixelRectToPdfBox(rect, vp);
    expect(box[0]).toBeCloseTo(40, 1);
    expect(box[2]).toBeCloseTo(240, 1);
    expect(box[3]).toBeCloseTo(842 - 80, 1);
    expect(box[1]).toBeCloseTo(842 - 160, 1);
    const back = pdfBoxToPixelRect(box, vp);
    expect(back.x).toBeCloseTo(rect.x, 1);
    expect(back.y).toBeCloseTo(rect.y, 1);
    await doc.destroy();
  });

  it("handles offset CropBox/MediaBox and Rotate 0/90/180/270", async () => {
    for (const rot of [0, 90, 180, 270]) {
      const doc = await pdfjsLib.getDocument({
        data: minimalPdf([100, 50, 695, 892], [120, 70, 675, 872], rot),
        verbosity: 0,
      }).promise;
      const page = await doc.getPage(1);
      const vp = page.getViewport({ scale: 1.25 });
      const rect = { x: 25, y: 30, w: 220, h: 90 };
      const box = pixelRectToPdfBox(rect, vp);
      expect(box[0]).toBeGreaterThanOrEqual(120 - 0.1);
      expect(box[1]).toBeGreaterThanOrEqual(70 - 0.1);
      expect(box[2]).toBeLessThanOrEqual(675 + 0.1);
      expect(box[3]).toBeLessThanOrEqual(872 + 0.1);
      const back = pdfBoxToPixelRect(box, vp);
      expect(Math.abs(back.x - rect.x)).toBeLessThan(1);
      expect(Math.abs(back.y - rect.y)).toBeLessThan(1);
      expect(Math.abs(back.w - rect.w)).toBeLessThan(1);
      expect(Math.abs(back.h - rect.h)).toBeLessThan(1);
      await doc.destroy();
    }
  });
});

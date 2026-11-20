import React from "react";

const ALLOWED_TAGS = new Set([
  "p",
  "br",
  "b",
  "strong",
  "i",
  "em",
  "u",
  "s",
  "del",
  "ul",
  "ol",
  "li",
  "blockquote",
  "pre",
  "code",
  "h1",
  "h2",
  "h3",
  "h4",
  "span",
  "div",
  "a",
  "hr",
]);

const DROP_SUBTREE_TAGS = new Set(["script", "style", "iframe", "object", "embed", "svg", "math"]);

export type SafeNode =
  | { kind: "text"; text: string }
  | { kind: "el"; tag: string; href?: string; children: SafeNode[] };

export function sanitizeHref(raw: string | null | undefined): string | null {
  if (!raw) return null;
  const trimmed = raw.trim();
  const normalized = trimmed.replace(/[\u0000-\u0020]+/g, "");
  if (!normalized || normalized.startsWith("#")) return trimmed || null;
  if (normalized.startsWith("//")) return null;
  if (!/^[a-zA-Z][a-zA-Z0-9+.-]*:/.test(normalized)) {
    return trimmed;
  }
  try {
    const parsed = new URL(normalized);
    if (parsed.protocol === "https:" || parsed.protocol === "http:" || parsed.protocol === "mailto:") {
      return parsed.href;
    }
  } catch {
    return null;
  }
  return null;
}

function walkDomNode(node: Node): SafeNode[] {
  if (node.nodeType === 3) {
    const txt = node.textContent || "";
    return txt ? [{ kind: "text", text: txt }] : [];
  }
  if (node.nodeType !== 1) return [];
  const el = node as Element;
  const tag = el.tagName.toLowerCase();
  if (DROP_SUBTREE_TAGS.has(tag)) return [];

  const children: SafeNode[] = [];
  for (let i = 0; i < el.childNodes.length; i += 1) {
    children.push(...walkDomNode(el.childNodes[i]));
  }

  if (!ALLOWED_TAGS.has(tag)) {
    return children;
  }
  if (tag === "a") {
    const safeUrl = sanitizeHref(el.getAttribute("href"));
    return [{ kind: "el", tag: safeUrl ? "a" : "span", href: safeUrl || undefined, children }];
  }
  return [{ kind: "el", tag, children }];
}

function fallbackStripHtml(html: string): SafeNode[] {
  const cleaned = html
    .replace(/<(script|style|iframe|object|embed|svg)[\s\S]*?<\/\1>/gi, "")
    .replace(/<[^>]+>/g, " ")
    .replace(/&nbsp;/g, " ")
    .replace(/&amp;/g, "&")
    .replace(/&lt;/g, "<")
    .replace(/&gt;/g, ">")
    .replace(/\s+/g, " ")
    .trim();
  return cleaned ? [{ kind: "text", text: cleaned }] : [];
}

/** Chuyển HTML người dùng thành cây phần tử React an toàn theo allowlist (không dùng DOM injection). */
export function sanitizeHtmlNodes(html: string): SafeNode[] {
  if (!html || !html.trim()) return [];
  if (typeof DOMParser === "undefined") {
    return fallbackStripHtml(html);
  }
  const doc = new DOMParser().parseFromString(html, "text/html");
  const out: SafeNode[] = [];
  for (let i = 0; i < doc.body.childNodes.length; i += 1) {
    out.push(...walkDomNode(doc.body.childNodes[i]));
  }
  return out;
}

function renderSafeNode(node: SafeNode, key: number): React.ReactNode {
  if (node.kind === "text") return node.text;
  const children = node.children.map((ch, idx) => renderSafeNode(ch, idx));
  if (node.tag === "br" || node.tag === "hr") {
    return React.createElement(node.tag, { key });
  }
  if (node.tag === "a" && node.href) {
    return React.createElement(
      "a",
      { key, href: node.href, target: "_blank", rel: "noopener noreferrer nofollow" },
      children,
    );
  }
  return React.createElement(node.tag, { key }, children);
}

export function SafeHtml({ html, className = "" }: { html: string | null | undefined; className?: string }) {
  const nodes = sanitizeHtmlNodes(html || "");
  if (nodes.length === 0) return null;
  return (
    <div className={`safe-html ${className}`.trim()}>
      {nodes.map((n, idx) => renderSafeNode(n, idx))}
    </div>
  );
}

import { useEffect, useRef } from "react";

/**
 * Polling định kỳ mỗi `intervalMs` khi tab trình duyệt đang hiển thị;
 * tự động tạm dừng khi tab bị ẩn và gọi lại ngay khi tab hiển thị lại.
 */
export function useVisibilityPolling(
  callback: () => void | Promise<void>,
  intervalMs = 25000,
  enabled = true,
): void {
  const cbRef = useRef(callback);
  cbRef.current = callback;

  useEffect(() => {
    if (!enabled || typeof document === "undefined") return;
    let timer: number | null = null;

    const clear = () => {
      if (timer !== null) {
        window.clearInterval(timer);
        timer = null;
      }
    };

    const start = () => {
      clear();
      if (document.visibilityState === "hidden") return;
      timer = window.setInterval(() => {
        if (document.visibilityState !== "hidden") {
          Promise.resolve(cbRef.current()).catch(() => {});
        }
      }, intervalMs);
    };

    const onVisChange = () => {
      if (document.visibilityState === "hidden") {
        clear();
      } else {
        Promise.resolve(cbRef.current()).catch(() => {});
        start();
      }
    };

    start();
    document.addEventListener("visibilitychange", onVisChange);
    return () => {
      clear();
      document.removeEventListener("visibilitychange", onVisChange);
    };
  }, [intervalMs, enabled]);
}

import type React from "react";

/** Dateiname aus Content-Disposition (bevorzugt filename*=UTF-8''…) */
function fileNameFrom(header: string | null, fallback: string): string {
  if (!header) return fallback;
  const star = /filename\*=UTF-8''([^;]+)/i.exec(header);
  if (star) {
    try { return decodeURIComponent(star[1]); } catch { /* weiter */ }
  }
  const plain = /filename="?([^";]+)"?/i.exec(header);
  return plain ? plain[1] : fallback;
}

/**
 * Lädt eine Datei per Klick herunter. Anders als ein einfacher Download-Link zeigt das bei einem Fehler
 * die Meldung des Servers an, statt eine JSON-Fehlerdatei zu speichern.
 */
export function downloadLinkHandler(url: string, fallbackName = "Export.xlsx") {
  return async (e: React.MouseEvent<HTMLElement>) => {
    e.preventDefault();
    const el = e.currentTarget;
    if (el.dataset.busy === "1") return;
    el.dataset.busy = "1";
    const oldOpacity = el.style.opacity;
    el.style.opacity = "0.5";
    try {
      const res = await fetch(url, { credentials: "same-origin" });
      if (!res.ok) {
        let detail = `HTTP ${res.status}`;
        try {
          const body = await res.json();
          if (body?.detail) detail = typeof body.detail === "string" ? body.detail : JSON.stringify(body.detail);
        } catch { /* kein JSON */ }
        window.alert(`Download fehlgeschlagen: ${detail}`);
        return;
      }
      const blob = await res.blob();
      const a = document.createElement("a");
      const href = URL.createObjectURL(blob);
      a.href = href;
      a.download = fileNameFrom(res.headers.get("content-disposition"), fallbackName);
      document.body.appendChild(a);
      a.click();
      a.remove();
      setTimeout(() => URL.revokeObjectURL(href), 10_000);
    } catch {
      window.alert("Download fehlgeschlagen: Server nicht erreichbar oder Verbindung abgebrochen.");
    } finally {
      el.dataset.busy = "";
      el.style.opacity = oldOpacity;
    }
  };
}

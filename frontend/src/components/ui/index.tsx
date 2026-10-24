"use client";

/**
 * Gemeinsame Bausteine des Design-Systems (Klassen stehen in globals.css).
 * Icons sind einfache Inline-SVGs im Lucide-Stil (keine Emojis, keine zusätzliche Abhängigkeit).
 */

import { ReactNode, useEffect, useId, useRef } from "react";

// ─── Icons ───────────────────────────────────────────────────────────────────

const PATHS: Record<string, ReactNode> = {
  dashboard: <><rect x="3" y="3" width="7" height="9" rx="1" /><rect x="14" y="3" width="7" height="5" rx="1" /><rect x="14" y="12" width="7" height="9" rx="1" /><rect x="3" y="16" width="7" height="5" rx="1" /></>,
  file: <><path d="M14 3H7a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h10a2 2 0 0 0 2-2V8z" /><path d="M14 3v5h5" /><path d="M9 13h6M9 17h6" /></>,
  box: <><path d="M21 8 12 3 3 8v8l9 5 9-5z" /><path d="m3 8 9 5 9-5M12 13v8" /></>,
  truck: <><path d="M3 6h11v10H3zM14 9h4l3 3v4h-7" /><circle cx="7" cy="18" r="2" /><circle cx="17" cy="18" r="2" /></>,
  upload: <><path d="M12 16V4M7 9l5-5 5 5" /><path d="M4 16v3a1 1 0 0 0 1 1h14a1 1 0 0 0 1-1v-3" /></>,
  database: <><ellipse cx="12" cy="5" rx="8" ry="3" /><path d="M4 5v6c0 1.7 3.6 3 8 3s8-1.3 8-3V5M4 11v6c0 1.7 3.6 3 8 3s8-1.3 8-3v-6" /></>,
  list: <><path d="M8 6h13M8 12h13M8 18h13M3 6h.01M3 12h.01M3 18h.01" /></>,
  chart: <><path d="M4 20V10M10 20V4M16 20v-8M22 20H2" /></>,
  schema: <><rect x="3" y="3" width="7" height="6" rx="1" /><rect x="14" y="3" width="7" height="6" rx="1" /><rect x="8.5" y="15" width="7" height="6" rx="1" /><path d="M6.5 9v3h11V9M12 12v3" /></>,
  ruler: <><path d="m3 17 14-14 4 4L7 21z" /><path d="m7 13 2 2M10 10l2 2M13 7l2 2" /></>,
  sheet: <><rect x="3" y="3" width="18" height="18" rx="2" /><path d="M3 9h18M3 15h18M9 3v18" /></>,
  flow: <><circle cx="6" cy="6" r="2.5" /><circle cx="18" cy="12" r="2.5" /><circle cx="6" cy="18" r="2.5" /><path d="M8.5 6H12a3 3 0 0 1 3 3v.5M8.5 18H12a3 3 0 0 0 3-3v-.5" /></>,
  cog: <><circle cx="12" cy="12" r="3" /><path d="M19.4 15a1.7 1.7 0 0 0 .3 1.8l.1.1a2 2 0 1 1-2.8 2.8l-.1-.1a1.7 1.7 0 0 0-1.8-.3 1.7 1.7 0 0 0-1 1.5V21a2 2 0 1 1-4 0v-.1a1.7 1.7 0 0 0-1.1-1.5 1.7 1.7 0 0 0-1.8.3l-.1.1a2 2 0 1 1-2.8-2.8l.1-.1a1.7 1.7 0 0 0 .3-1.8 1.7 1.7 0 0 0-1.5-1H3a2 2 0 1 1 0-4h.1a1.7 1.7 0 0 0 1.5-1.1 1.7 1.7 0 0 0-.3-1.8l-.1-.1a2 2 0 1 1 2.8-2.8l.1.1a1.7 1.7 0 0 0 1.8.3H9a1.7 1.7 0 0 0 1-1.5V3a2 2 0 1 1 4 0v.1a1.7 1.7 0 0 0 1 1.5 1.7 1.7 0 0 0 1.8-.3l.1-.1a2 2 0 1 1 2.8 2.8l-.1.1a1.7 1.7 0 0 0-.3 1.8V9a1.7 1.7 0 0 0 1.5 1H21a2 2 0 1 1 0 4h-.1a1.7 1.7 0 0 0-1.5 1z" /></>,
  menu: <><path d="M4 6h16M4 12h16M4 18h16" /></>,
  x: <><path d="M18 6 6 18M6 6l12 12" /></>,
  plus: <><path d="M12 5v14M5 12h14" /></>,
  trash: <><path d="M3 6h18M8 6V4h8v2M6 6l1 14h10l1-14M10 11v6M14 11v6" /></>,
  grip: <><circle cx="9" cy="6" r="1" /><circle cx="15" cy="6" r="1" /><circle cx="9" cy="12" r="1" /><circle cx="15" cy="12" r="1" /><circle cx="9" cy="18" r="1" /><circle cx="15" cy="18" r="1" /></>,
  lock: <><rect x="5" y="11" width="14" height="10" rx="2" /><path d="M8 11V7a4 4 0 0 1 8 0v4" /></>,
  check: <><path d="m5 12 5 5L20 7" /></>,
  alert: <><path d="M12 3 2 21h20z" /><path d="M12 10v5M12 18h.01" /></>,
  download: <><path d="M12 4v12M7 11l5 5 5-5" /><path d="M4 18v1a1 1 0 0 0 1 1h14a1 1 0 0 0 1-1v-1" /></>,
  user: <><circle cx="12" cy="8" r="4" /><path d="M4 21a8 8 0 0 1 16 0" /></>,
  chevron: <><path d="m6 9 6 6 6-6" /></>,
  edit: <><path d="M4 20h4L19 9l-4-4L4 16z" /><path d="m14 6 4 4" /></>,
  send: <><path d="M5 12h14M13 6l6 6-6 6" /></>,
  image: <><rect x="3" y="4" width="18" height="16" rx="2" /><circle cx="9" cy="10" r="1.5" /><path d="m21 16-5-5-9 9" /></>,
  refresh: <><path d="M20 11a8 8 0 0 0-14.5-4M4 5v4h4M4 13a8 8 0 0 0 14.5 4M20 19v-4h-4" /></>,
  chat: <><path d="M21 12a8 8 0 0 1-11.6 7.1L4 20l1-4.6A8 8 0 1 1 21 12z" /></>,
  sparkle: <><path d="M12 3l1.8 5.2L19 10l-5.2 1.8L12 17l-1.8-5.2L5 10l5.2-1.8z" /><path d="M19 16l.7 2 2 .7-2 .7-.7 2-.7-2-2-.7 2-.7z" /></>,
  stop: <><rect x="6" y="6" width="12" height="12" rx="2" /></>,
  layers: <><path d="m12 3 9 5-9 5-9-5z" /><path d="m3 13 9 5 9-5M3 17.5l9 5 9-5" /></>,
};

export function Icon({ name, className = "h-4 w-4" }: { name: keyof typeof PATHS | string; className?: string }) {
  return (
    <svg aria-hidden="true" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={1.8}
      strokeLinecap="round" strokeLinejoin="round" className={className}>
      {PATHS[name] ?? null}
    </svg>
  );
}

// ─── Seitenkopf, Karten, Hinweise ────────────────────────────────────────────

export function PageHeader({ title, subtitle, actions }: { title: string; subtitle?: ReactNode; actions?: ReactNode }) {
  return (
    <div className="mb-6 flex flex-wrap items-start justify-between gap-3">
      <div className="min-w-0">
        <h1 className="text-2xl font-semibold tracking-tight text-gray-900">{title}</h1>
        {subtitle && <p className="mt-1 max-w-3xl text-sm text-gray-500">{subtitle}</p>}
      </div>
      {actions && <div className="flex flex-wrap items-center gap-2">{actions}</div>}
    </div>
  );
}

export function Card({ title, actions, children, className = "", pad = true }: {
  title?: ReactNode; actions?: ReactNode; children: ReactNode; className?: string; pad?: boolean;
}) {
  return (
    <section className={`card ${className}`}>
      {(title || actions) && (
        <header className="flex flex-wrap items-center justify-between gap-2 border-b border-gray-100 px-5 py-3">
          <h2 className="text-sm font-semibold text-gray-800">{title}</h2>
          {actions && <div className="flex items-center gap-2">{actions}</div>}
        </header>
      )}
      <div className={pad ? "p-5" : ""}>{children}</div>
    </section>
  );
}

type Tone = "gray" | "blue" | "green" | "amber" | "red" | "violet";
export function Badge({ tone = "gray", children, title }: { tone?: Tone; children: ReactNode; title?: string }) {
  return <span title={title} className={`badge badge-${tone}`}>{children}</span>;
}

const ALERT_TONES: Record<string, string> = {
  error: "border-red-200 bg-red-50 text-red-800",
  warn: "border-amber-200 bg-amber-50 text-amber-900",
  info: "border-blue-200 bg-blue-50 text-blue-900",
  success: "border-emerald-200 bg-emerald-50 text-emerald-800",
};
export function Alert({ tone = "info", children, onClose }: { tone?: "error" | "warn" | "info" | "success"; children: ReactNode; onClose?: () => void }) {
  return (
    <div role={tone === "error" ? "alert" : "status"} className={`flex items-start gap-2 rounded-lg border px-3.5 py-2.5 text-sm ${ALERT_TONES[tone]}`}>
      <div className="min-w-0 flex-1">{children}</div>
      {onClose && (
        <button type="button" onClick={onClose} aria-label="Hinweis schließen" className="rounded p-0.5 opacity-70 hover:opacity-100">
          <Icon name="x" className="h-4 w-4" />
        </button>
      )}
    </div>
  );
}

export function EmptyState({ title, text, action }: { title: string; text?: ReactNode; action?: ReactNode }) {
  return (
    <div className="rounded-xl border border-dashed border-gray-300 bg-white px-6 py-10 text-center">
      <p className="text-sm font-medium text-gray-800">{title}</p>
      {text && <p className="mx-auto mt-1 max-w-md text-sm text-gray-500">{text}</p>}
      {action && <div className="mt-4">{action}</div>}
    </div>
  );
}

export function Spinner({ label = "Lade…" }: { label?: string }) {
  return (
    <div className="flex items-center gap-2 py-6 text-sm text-gray-500" role="status">
      <span className="h-4 w-4 animate-spin rounded-full border-2 border-gray-300 border-t-blue-600" />
      {label}
    </div>
  );
}

export function Field({ label, hint, children, className = "" }: { label: string; hint?: ReactNode; children: ReactNode; className?: string }) {
  const id = useId();
  return (
    <div className={className}>
      <label htmlFor={id} className="label">{label}</label>
      <div id={id}>{children}</div>
      {hint && <p className="hint">{hint}</p>}
    </div>
  );
}

export function Tabs<T extends string>({ tabs, value, onChange }: { tabs: { id: T; label: string; badge?: ReactNode }[]; value: T; onChange: (v: T) => void }) {
  return (
    <div role="tablist" className="mb-5 flex flex-wrap gap-x-1 border-b border-gray-200">
      {tabs.map((t) => (
        <button key={t.id} role="tab" aria-selected={value === t.id} type="button" onClick={() => onChange(t.id)}
          className={`-mb-px flex items-center gap-1.5 whitespace-nowrap border-b-2 px-4 py-2.5 text-sm font-medium transition-colors ${
            value === t.id ? "border-blue-600 text-blue-700" : "border-transparent text-gray-500 hover:text-gray-800"}`}>
          {t.label}{t.badge}
        </button>
      ))}
    </div>
  );
}

// ─── Dialog ──────────────────────────────────────────────────────────────────

export function Modal({ title, onClose, children, footer, wide = false }: {
  title: string; onClose: () => void; children: ReactNode; footer?: ReactNode; wide?: boolean;
}) {
  const ref = useRef<HTMLDivElement>(null);
  useEffect(() => {
    const prev = document.activeElement as HTMLElement | null;
    ref.current?.focus();
    const onKey = (e: KeyboardEvent) => { if (e.key === "Escape") onClose(); };
    document.addEventListener("keydown", onKey);
    return () => { document.removeEventListener("keydown", onKey); prev?.focus?.(); };
  }, [onClose]);
  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-slate-900/50 p-4" onMouseDown={(e) => { if (e.target === e.currentTarget) onClose(); }}>
      <div ref={ref} tabIndex={-1} role="dialog" aria-modal="true" aria-label={title}
        className={`flex max-h-[90vh] w-full flex-col rounded-xl bg-white shadow-pop outline-none ${wide ? "max-w-3xl" : "max-w-lg"}`}>
        <header className="flex items-center justify-between border-b border-gray-100 px-5 py-3">
          <h2 className="text-base font-semibold text-gray-900">{title}</h2>
          <button type="button" onClick={onClose} aria-label="Schließen" className="rounded p-1 text-gray-500 hover:bg-gray-100"><Icon name="x" /></button>
        </header>
        <div className="overflow-y-auto p-5">{children}</div>
        {footer && <footer className="flex justify-end gap-2 border-t border-gray-100 px-5 py-3">{footer}</footer>}
      </div>
    </div>
  );
}

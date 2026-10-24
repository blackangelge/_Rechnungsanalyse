/**
 * Seitenleiste (Desktop) bzw. ausklappbare Leiste (Handy/Tablet).
 *
 * Gruppen: Arbeiten · Schema & Auswertung · Verarbeitung · Einstellungen.
 * Das JS-Badge unten zeigt, ob React hydratisiert wurde (Diagnose nach Cache-Problemen auf dem NAS).
 */

"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { useEffect, useState } from "react";
import { Icon } from "@/components/ui";
import { unitsApi } from "@/lib/api";

interface NavItem { href: string; label: string; icon: string }
interface NavGroup { title: string; items: NavItem[] }

export const NAV_GROUPS: NavGroup[] = [
  {
    title: "Arbeiten",
    items: [
      { href: "/dashboard", label: "Dashboard", icon: "dashboard" },
      { href: "/belege", label: "Belege", icon: "file" },
      { href: "/artikel", label: "Artikel", icon: "box" },
      { href: "/lieferanten", label: "Lieferanten", icon: "truck" },
      { href: "/imports/new", label: "Neuer Import", icon: "upload" },
    ],
  },
  {
    title: "Schema & Auswertung",
    items: [
      { href: "/schema", label: "Schema", icon: "schema" },
      { href: "/einheiten", label: "Einheiten", icon: "ruler" },
      { href: "/export-vorlagen", label: "Export-Vorlagen", icon: "sheet" },
      { href: "/daten", label: "Daten", icon: "database" },
      { href: "/assistent", label: "KI-Assistent", icon: "sparkle" },
    ],
  },
  {
    title: "Verarbeitung",
    items: [
      { href: "/settings/workflows", label: "Workflows", icon: "flow" },
      { href: "/tasks", label: "Tasks", icon: "list" },
      { href: "/logs", label: "KI-Statistiken", icon: "chart" },
    ],
  },
  {
    title: "Einstellungen",
    items: [
      { href: "/settings/ai", label: "KI-Konfiguration", icon: "cog" },
      { href: "/settings/ai-test", label: "KI testen", icon: "cog" },
      { href: "/settings/prompts", label: "Systemprompts", icon: "cog" },
      { href: "/settings/image", label: "Bildeinstellungen", icon: "cog" },
      { href: "/settings/export", label: "Export (alt)", icon: "cog" },
      { href: "/settings/automation", label: "Automatisierung", icon: "cog" },
      { href: "/settings/backup", label: "Backups", icon: "cog" },
      { href: "/settings/data-backup", label: "Daten-Backup", icon: "cog" },
    ],
  },
];

function isActive(pathname: string, href: string): boolean {
  return pathname === href || pathname.startsWith(href + "/");
}

export default function Nav({
  username = null,
  onLogout,
  children,
}: {
  username?: string | null;
  onLogout?: () => void;
  children: React.ReactNode;
}) {
  const pathname = usePathname();
  const [jsOk, setJsOk] = useState(false);
  const [open, setOpen] = useState(false);
  const [unitReview, setUnitReview] = useState(0);              // offene Schreibweisen in der Einheiten-Prüfliste
  const full = pathname === "/assistent" || pathname.startsWith("/assistent/");   // Vollbild-Seite ohne Rand

  useEffect(() => { setJsOk(true); }, []);
  useEffect(() => { setOpen(false); }, [pathname]);
  useEffect(() => {
    // Zähler der Einheiten-Prüfliste: beim Seitenwechsel, alle 60 s und sofort, wenn die Einheiten-Seite etwas ändert
    let cancelled = false;
    const refresh = () => { unitsApi.reviewCount().then((c) => { if (!cancelled) setUnitReview(c.aliases); }).catch(() => undefined); };
    refresh();
    const timer = window.setInterval(refresh, 60_000);
    window.addEventListener("ra-units-review", refresh);
    return () => { cancelled = true; window.clearInterval(timer); window.removeEventListener("ra-units-review", refresh); };
  }, [pathname]);

  const sidebar = (
    <div className="flex h-full flex-col bg-[var(--ra-sidebar)] text-[var(--ra-sidebar-text)]">
      <Link href="/dashboard" className="flex items-center gap-2.5 px-5 py-5 text-white">
        <span className="flex h-8 w-8 items-center justify-center rounded-lg bg-blue-600"><Icon name="layers" className="h-4.5 w-4.5" /></span>
        <span className="text-base font-semibold tracking-tight">Rechnungsanalyse</span>
      </Link>
      <nav aria-label="Hauptnavigation" className="flex-1 space-y-5 overflow-y-auto px-3 pb-4">
        {NAV_GROUPS.map((g) => (
          <div key={g.title}>
            <p className="px-2 pb-1.5 text-[11px] font-semibold uppercase tracking-wider text-slate-500">{g.title}</p>
            <ul className="space-y-0.5">
              {g.items.map((it) => {
                const active = isActive(pathname, it.href);
                return (
                  <li key={it.href}>
                    <Link href={it.href} aria-current={active ? "page" : undefined}
                      className={`flex items-center gap-2.5 rounded-lg px-2.5 py-2 text-sm transition-colors ${
                        active ? "bg-[var(--ra-sidebar-active)] font-medium text-white" : "hover:bg-white/10 hover:text-white"}`}>
                      <Icon name={it.icon} className="h-4 w-4 shrink-0 opacity-90" />
                      <span className="truncate">{it.label}</span>
                      {it.href === "/einheiten" && unitReview > 0 && (
                        <span className="ml-auto rounded-full bg-amber-400 px-1.5 text-[11px] font-semibold text-slate-900" title={`${unitReview} unbekannte Schreibweise(n) in der Prüfliste`}
                          data-testid="nav-units-review">{unitReview > 99 ? "99+" : unitReview}</span>
                      )}
                    </Link>
                  </li>
                );
              })}
            </ul>
          </div>
        ))}
      </nav>
      <div className="border-t border-white/10 p-3">
        {username && (
          <div className="flex items-center justify-between gap-2">
            <Link href="/settings/account" title="Passwort ändern" className="flex min-w-0 items-center gap-2 rounded-lg px-2 py-1.5 text-sm hover:bg-white/10">
              <Icon name="user" className="h-4 w-4 shrink-0" /><span className="truncate">{username}</span>
            </Link>
            {onLogout && (
              <button type="button" onClick={onLogout} className="rounded-lg border border-white/20 px-2.5 py-1 text-xs hover:bg-white/10">Abmelden</button>
            )}
          </div>
        )}
        <p className="mt-2 px-2 text-[11px]">
          <span className={jsOk ? "text-emerald-400" : "text-red-400"}>{jsOk ? "JS ✓" : "JS ✗"}</span>
        </p>
      </div>
    </div>
  );

  return (
    <div className="min-h-screen lg:flex">
      {/* Desktop */}
      <aside className="sticky top-0 hidden h-screen w-60 shrink-0 lg:block">{sidebar}</aside>

      {/* Handy/Tablet: Kopfleiste + Schublade */}
      <header className="sticky top-0 z-30 flex items-center gap-3 border-b border-gray-200 bg-white px-4 py-2.5 lg:hidden">
        <button type="button" aria-label="Menü öffnen" aria-expanded={open} onClick={() => setOpen(true)} className="btn btn-ghost btn-sm"><Icon name="menu" className="h-5 w-5" /></button>
        <span className="text-sm font-semibold text-gray-900">Rechnungsanalyse</span>
      </header>
      {open && (
        <div className="fixed inset-0 z-40 lg:hidden">
          <div className="absolute inset-0 bg-slate-900/50" onClick={() => setOpen(false)} />
          <div className="absolute inset-y-0 left-0 w-64 shadow-pop">{sidebar}</div>
        </div>
      )}

      {full ? (
        <main className="h-[calc(100dvh-49px)] min-w-0 flex-1 overflow-hidden lg:h-screen">{children}</main>
      ) : (
        <main className="min-w-0 flex-1 overflow-x-hidden">
          <div className="mx-auto max-w-[1400px] p-4 sm:p-6 lg:p-8">{children}</div>
        </main>
      )}
    </div>
  );
}

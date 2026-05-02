/**
 * Hauptnavigationsleiste der Anwendung.
 *
 * Enthält Links zu allen Hauptbereichen und ein Einstellungen-Dropdown.
 * Das JS-Badge (grün "JS ✓" / rot "JS ✗") zeigt an, ob React korrekt
 * hydratisiert wurde — hilft beim Diagnostizieren von Hydration-Fehlern
 * nach Cache-Problemen auf dem NAS.
 *
 * Das Einstellungen-Dropdown schließt automatisch bei Klick außerhalb
 * (mousedown-Listener auf document).
 */

"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { useEffect, useRef, useState } from "react";

const SETTINGS_LINKS = [
  { href: "/settings/ai",         label: "KI-Einstellungen" },
  { href: "/settings/prompts",    label: "Systemprompts" },
  { href: "/settings/image",      label: "Bildeinstellungen" },
  { href: "/settings/backup",     label: "Backups / Wiederherstellung" },
];

const MAIN_LINKS = [
  { href: "/dashboard",    label: "Dashboard" },
  { href: "/belege",       label: "Belege" },
  { href: "/lieferanten",  label: "Lieferanten" },
  { href: "/tasks",        label: "Tasks" },
  { href: "/logs",         label: "KI-Statistiken" },
];

export default function Nav() {
  const pathname = usePathname();
  const [jsOk, setJsOk] = useState(false);
  const [settingsOpen, setSettingsOpen] = useState(false);
  const dropdownRef = useRef<HTMLDivElement>(null);

  useEffect(() => { setJsOk(true); }, []);

  // Dropdown schließen bei Klick außerhalb
  useEffect(() => {
    function handle(e: MouseEvent) {
      if (dropdownRef.current && !dropdownRef.current.contains(e.target as Node)) {
        setSettingsOpen(false);
      }
    }
    document.addEventListener("mousedown", handle);
    return () => document.removeEventListener("mousedown", handle);
  }, []);

  const isSettingsActive = SETTINGS_LINKS.some(
    (l) => pathname === l.href || pathname.startsWith(l.href + "/")
  );

  return (
    <nav className="border-b bg-white shadow-sm">
      <div className="mx-auto flex max-w-7xl items-center justify-between px-6 py-3">
        {/* Logo */}
        <Link href="/dashboard" className="text-lg font-bold tracking-tight text-blue-700">
          Rechnungsanalyse
          <span className={`ml-2 text-xs font-normal px-1.5 py-0.5 rounded ${jsOk ? "bg-green-100 text-green-700" : "bg-red-100 text-red-700"}`}>
            {jsOk ? "JS ✓" : "JS ✗"}
          </span>
        </Link>

        {/* Navigationslinks */}
        <ul className="flex items-center gap-1">
          {MAIN_LINKS.map(({ href, label }) => {
            const isActive = pathname === href || pathname.startsWith(href + "/");
            return (
              <li key={href}>
                <Link
                  href={href}
                  className={[
                    "rounded px-3 py-2 text-sm font-medium transition-colors",
                    isActive
                      ? "bg-blue-600 text-white"
                      : "text-gray-600 hover:bg-gray-100 hover:text-gray-900",
                  ].join(" ")}
                >
                  {label}
                </Link>
              </li>
            );
          })}

          {/* Einstellungen-Dropdown */}
          <li ref={dropdownRef} className="relative">
            <button
              onClick={() => setSettingsOpen((o) => !o)}
              className={[
                "flex items-center gap-1 rounded px-3 py-2 text-sm font-medium transition-colors",
                isSettingsActive || settingsOpen
                  ? "bg-blue-600 text-white"
                  : "text-gray-600 hover:bg-gray-100 hover:text-gray-900",
              ].join(" ")}
            >
              Einstellungen
              <svg
                className={`h-3.5 w-3.5 transition-transform ${settingsOpen ? "rotate-180" : ""}`}
                fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2}
              >
                <path strokeLinecap="round" strokeLinejoin="round" d="M19 9l-7 7-7-7" />
              </svg>
            </button>

            {settingsOpen && (
              <div className="absolute right-0 z-50 mt-1 w-56 rounded-lg border border-gray-200 bg-white shadow-lg">
                {SETTINGS_LINKS.map(({ href, label }) => {
                  const isActive = pathname === href || pathname.startsWith(href + "/");
                  return (
                    <Link
                      key={href}
                      href={href}
                      onClick={() => setSettingsOpen(false)}
                      className={[
                        "block px-4 py-2.5 text-sm transition-colors first:rounded-t-lg last:rounded-b-lg",
                        isActive
                          ? "bg-blue-50 font-medium text-blue-700"
                          : "text-gray-700 hover:bg-gray-50",
                      ].join(" ")}
                    >
                      {label}
                    </Link>
                  );
                })}
              </div>
            )}
          </li>
        </ul>
      </div>
    </nav>
  );
}

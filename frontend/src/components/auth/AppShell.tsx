"use client";

import { usePathname, useRouter } from "next/navigation";
import { useEffect, useState } from "react";
import Nav from "@/components/Nav";
import { AuthInfo, authApi } from "@/lib/api";

/**
 * Rahmen um alle Seiten: prüft die Anmeldung und zeigt Navigation + Inhalt erst danach.
 * Die Anmeldeseite selbst wird ohne Navigation dargestellt.
 *
 * Hydration: Der Anfangszustand "checking" rendert auf Server und Client identisch
 * (nur ein Platzhalter) — erst im Effekt wird der Inhalt eingeblendet.
 */
export default function AppShell({ children }: { children: React.ReactNode }) {
  const pathname = usePathname();
  const router = useRouter();
  const [state, setState] = useState<"checking" | "authed">("checking");
  const [info, setInfo] = useState<AuthInfo | null>(null);
  const onLogin = pathname === "/login" || pathname.startsWith("/login/");

  useEffect(() => {
    if (onLogin) return;
    let cancelled = false;
    authApi
      .me()
      .then((data) => {
        if (cancelled) return;
        setInfo(data);
        setState("authed");
      })
      .catch((err) => {
        if (cancelled) return;
        if (err?.response?.status === 401) {
          const next = encodeURIComponent(window.location.pathname + window.location.search);
          router.replace(`/login?next=${next}`);
        } else {
          // Backend nicht erreichbar o. ä.: Seite trotzdem zeigen, die Seiten melden ihre Fehler selbst
          setState("authed");
        }
      });
    return () => {
      cancelled = true;
    };
  }, [onLogin, router]);

  if (onLogin) {
    return <main className="mx-auto flex min-h-screen max-w-md items-center p-6"><div className="w-full">{children}</div></main>;
  }

  async function logout() {
    try {
      await authApi.logout();
    } finally {
      window.location.href = "/login";
    }
  }

  if (state !== "authed") {
    return <div className="py-16 text-center text-sm text-gray-400">Lade…</div>;
  }

  return (
    <Nav username={info?.username ?? null} onLogout={info?.username ? logout : undefined}>
      {children}
    </Nav>
  );
}

import type { Metadata } from "next";
import AppShell from "@/components/auth/AppShell";
import "./globals.css";

export const metadata: Metadata = {
  title: "Rechnungsanalyse",
  description: "Invoice analysis application",
};

/**
 * Root-Layout der Anwendung.
 * Bindet die Navigationsleiste ein und stellt das Basis-Layout bereit.
 * Alle Seiten erben dieses Layout automatisch (Next.js App Router).
 */
export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="de">
      <body className="min-h-screen antialiased">
        {/* Anmeldeprüfung, Navigationsleiste und Seiteninhalt (siehe AppShell) */}
        <AppShell>{children}</AppShell>
      </body>
    </html>
  );
}

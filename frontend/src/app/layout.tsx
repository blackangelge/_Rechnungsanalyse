import type { Metadata } from "next";
import Nav from "@/components/Nav";
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
      <body className="min-h-screen bg-gray-50 text-gray-900 antialiased">
        {/* Globale Navigationsleiste */}
        <Nav />
        {/* Seiteninhalt — overflow-x-hidden für Full-Width-Breakout in Belege */}
        <main className="overflow-x-hidden">
          <div className="mx-auto max-w-7xl p-6">{children}</div>
        </main>
      </body>
    </html>
  );
}

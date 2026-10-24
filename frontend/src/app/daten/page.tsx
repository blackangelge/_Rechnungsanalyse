"use client";

import { useEffect } from "react";
import Link from "next/link";
import TableBrowser from "@/components/daten/TableBrowser";

export default function DatenPage() {
  // Alter Link ?tab=assistent: der KI-Assistent hat jetzt eine eigene Seite
  useEffect(() => {
    if (new URLSearchParams(window.location.search).get("tab") === "assistent") window.location.replace("/assistent");
  }, []);

  return (
    <div className="w-full">
      <h1 className="mb-1 text-2xl font-semibold tracking-tight text-gray-900">Daten</h1>
      <p className="mb-4 text-sm text-gray-500">
        Die Datenbank direkt ansehen und durchsuchen. Bearbeiten ist ein bewusster Schalter; Zugangsdaten und API-Schlüssel sind ausgenommen.
        Fragen an die KI stellst du im <Link href="/assistent" className="text-blue-600 underline">KI-Assistenten</Link>.
      </p>
      <TableBrowser />
    </div>
  );
}

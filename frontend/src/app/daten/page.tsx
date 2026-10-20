"use client";

import React, { useEffect, useState } from "react";
import Assistant from "@/components/daten/Assistant";
import TableBrowser from "@/components/daten/TableBrowser";

type Tab = "tabellen" | "assistent";

export default function DatenPage() {
  const [tab, setTab] = useState<Tab>("tabellen");

  // Tab aus der Adresszeile (?tab=assistent) — im Mount-Effekt, damit Server- und Client-Render übereinstimmen
  useEffect(() => {
    const t = new URLSearchParams(window.location.search).get("tab");
    if (t === "assistent") setTab("assistent");
  }, []);

  function select(t: Tab) {
    setTab(t);
    const url = new URL(window.location.href);
    url.searchParams.set("tab", t);
    window.history.replaceState(null, "", url.pathname + url.search);
  }

  const tabBtn = (t: Tab, label: string) => (
    <button
      onClick={() => select(t)}
      className={`rounded-t px-4 py-2 text-sm font-medium ${tab === t ? "border-b-2 border-blue-600 text-blue-700" : "text-gray-500 hover:text-gray-800"}`}
      data-testid={`tab-${t}`}
    >
      {label}
    </button>
  );

  return (
    <div className="relative left-1/2 w-screen -translate-x-1/2 px-6">
      <h1 className="mb-1 text-2xl font-bold text-gray-900">Daten</h1>
      <p className="mb-3 text-sm text-gray-500">
        Die Datenbank direkt ansehen und durchsuchen — oder die KI fragen. Bearbeiten ist ein bewusster Schalter; Zugangsdaten und API-Schlüssel sind ausgenommen.
      </p>
      <div className="mb-4 flex gap-1 border-b">
        {tabBtn("tabellen", "Tabellen")}
        {tabBtn("assistent", "KI-Assistent")}
      </div>
      {tab === "tabellen" ? <TableBrowser /> : <Assistant />}
    </div>
  );
}

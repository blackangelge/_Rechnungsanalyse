"use client";

import { useState } from "react";
import { authApi, extractApiError } from "@/lib/api";

export default function AccountPage() {
  const [current, setCurrent] = useState("");
  const [next, setNext] = useState("");
  const [repeat, setRepeat] = useState("");
  const [busy, setBusy] = useState(false);
  const [msg, setMsg] = useState<{ ok: boolean; text: string } | null>(null);

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    setMsg(null);
    if (next.length < 8) return setMsg({ ok: false, text: "Das neue Passwort braucht mindestens 8 Zeichen." });
    if (next !== repeat) return setMsg({ ok: false, text: "Die beiden neuen Passwörter stimmen nicht überein." });
    setBusy(true);
    try {
      await authApi.changePassword(current, next);
      setCurrent("");
      setNext("");
      setRepeat("");
      setMsg({ ok: true, text: "Passwort geändert. Auf anderen Geräten sind Sie jetzt abgemeldet." });
    } catch (err) {
      setMsg({ ok: false, text: extractApiError(err, "Passwort konnte nicht geändert werden") });
    } finally {
      setBusy(false);
    }
  }

  const input = "w-full rounded border border-gray-300 px-3 py-2 text-sm";
  return (
    <div className="max-w-md">
      <h1 className="mb-1 text-2xl font-bold text-gray-900">Passwort ändern</h1>
      <p className="mb-6 text-sm text-gray-500">
        Ein hier geändertes Passwort bleibt auch nach einem Neustart gültig; die Werte in der .env legen nur fehlende Benutzer an.
      </p>
      {msg && (
        <div className={`mb-4 rounded-md border p-3 text-sm ${msg.ok ? "border-green-200 bg-green-50 text-green-700" : "border-red-200 bg-red-50 text-red-700"}`}>
          {msg.text}
        </div>
      )}
      <form onSubmit={submit} className="space-y-4 rounded-lg border bg-white p-6 shadow-sm">
        <div>
          <label htmlFor="cur" className="mb-1 block text-sm font-medium text-gray-700">Aktuelles Passwort</label>
          <input id="cur" type="password" autoComplete="current-password" required value={current} onChange={(e) => setCurrent(e.target.value)} className={input} />
        </div>
        <div>
          <label htmlFor="new" className="mb-1 block text-sm font-medium text-gray-700">Neues Passwort (mind. 8 Zeichen)</label>
          <input id="new" type="password" autoComplete="new-password" required value={next} onChange={(e) => setNext(e.target.value)} className={input} />
        </div>
        <div>
          <label htmlFor="rep" className="mb-1 block text-sm font-medium text-gray-700">Neues Passwort wiederholen</label>
          <input id="rep" type="password" autoComplete="new-password" required value={repeat} onChange={(e) => setRepeat(e.target.value)} className={input} />
        </div>
        <button type="submit" disabled={busy} className="rounded bg-blue-600 px-5 py-2 text-sm font-medium text-white hover:bg-blue-700 disabled:opacity-50">
          {busy ? "Speichere…" : "Passwort ändern"}
        </button>
      </form>
    </div>
  );
}

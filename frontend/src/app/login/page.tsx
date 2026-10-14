"use client";

import { useState } from "react";
import { authApi, extractApiError } from "@/lib/api";

/** Nur interne Ziele erlauben (kein Open-Redirect auf fremde Seiten). */
function safeNext(): string {
  const raw = new URLSearchParams(window.location.search).get("next") ?? "";
  return raw.startsWith("/") && !raw.startsWith("//") && !raw.startsWith("/login") ? raw : "/dashboard";
}

export default function LoginPage() {
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    setBusy(true);
    setError(null);
    try {
      await authApi.login(username, password);
      window.location.href = safeNext();   // harter Wechsel: Navigation lädt mit gültiger Sitzung neu
    } catch (err) {
      setError(extractApiError(err, "Anmeldung fehlgeschlagen"));
      setBusy(false);
    }
  }

  return (
    <form onSubmit={submit} className="rounded-lg border bg-white p-8 shadow-sm" data-testid="login-form">
      <h1 className="mb-1 text-2xl font-bold text-blue-700">Rechnungsanalyse</h1>
      <p className="mb-6 text-sm text-gray-500">Bitte anmelden.</p>

      {error && (
        <div className="mb-4 rounded-md border border-red-200 bg-red-50 p-3 text-sm text-red-700" role="alert" data-testid="login-error">
          {error}
        </div>
      )}

      <label htmlFor="username" className="mb-1 block text-sm font-medium text-gray-700">Benutzername</label>
      <input
        id="username"
        autoComplete="username"
        autoFocus
        required
        value={username}
        onChange={(e) => setUsername(e.target.value)}
        className="mb-4 w-full rounded border border-gray-300 px-3 py-2 text-sm"
      />

      <label htmlFor="password" className="mb-1 block text-sm font-medium text-gray-700">Passwort</label>
      <input
        id="password"
        type="password"
        autoComplete="current-password"
        required
        value={password}
        onChange={(e) => setPassword(e.target.value)}
        className="mb-6 w-full rounded border border-gray-300 px-3 py-2 text-sm"
      />

      <button
        type="submit"
        disabled={busy}
        className="w-full rounded bg-blue-600 px-4 py-2 text-sm font-medium text-white hover:bg-blue-700 disabled:opacity-50"
      >
        {busy ? "Melde an…" : "Anmelden"}
      </button>

      <p className="mt-4 text-xs text-gray-400">
        Noch kein Benutzer? Er wird beim Start aus der .env angelegt (INITIAL_ADMIN_USER / INITIAL_ADMIN_PASSWORD).
      </p>
    </form>
  );
}

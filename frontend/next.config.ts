import type { NextConfig } from "next";

const nextConfig: NextConfig = {
  // StrictMode in dev führt zu doppelten useEffect-Aufrufen → zwei parallele API-Anfragen
  // und kann Hydration-Probleme auf dem NAS/Docker-Setup verursachen
  reactStrictMode: false,
  // NAS-Hostname muss explizit erlaubt werden, sonst blockiert Next.js 16
  // cross-origin Anfragen zu /_next/webpack-hmr (HMR WebSocket)
  // Erlaubte Dev-Origins für HMR WebSocket — per Umgebungsvariable konfigurierbar.
  // Beispiel docker-compose: ALLOWED_DEV_ORIGINS=nas,192.168.1.100
  allowedDevOrigins: process.env.ALLOWED_DEV_ORIGINS
    ? process.env.ALLOWED_DEV_ORIGINS.split(",").map((s) => s.trim()).filter(Boolean)
    : [],
  experimental: {
    // Next.js' eigener rewrites()-Proxy (siehe unten) bricht Anfragen an das Backend
    // standardmäßig nach 30s ab (Node.js "socket hang up" / ECONNRESET — UNABHÄNGIG
    // von jedem axios-Timeout im Frontend-Code und von jedem Backend-seitigen
    // httpx-Timeout, da der Next.js-Dev-Server selbst die Verbindung kappt, bevor
    // die Antwort überhaupt zurückkommt). Betraf v.a. POST /api/ai-clients/{id}/test
    // bei langsameren/lokalen KI-Modellen — das Backend hat die Anfrage nie mit
    // einem Fehler beendet (kein Log-Eintrag, kein Traceback), weil der Next.js-
    // Proxy die Verbindung schon vorher zwangsweise beendet hat. Muss über dem
    // längsten Backend-Timeout liegen (ai_test_service.REQUEST_TIMEOUT_SECONDS=900,
    // ai_service.REQUEST_TIMEOUT_SECONDS=900) — siehe Bekannte Fallstricke in CLAUDE.md.
    proxyTimeout: 920000,
  },
  async rewrites() {
    return [
      {
        source: "/api/:path*",
        destination: `${process.env.NEXT_PUBLIC_API_URL}/api/:path*`,
      },
    ];
  },
};

export default nextConfig;

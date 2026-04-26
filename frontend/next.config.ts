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

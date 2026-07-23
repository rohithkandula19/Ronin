/** @type {import('next').NextConfig} */

// Public base URL of the hosted Ronin backend. Not a secret — it's the same
// origin published in docs/beta/deploy-backend.md. Overridable at build time
// via RONIN_API_ORIGIN (e.g. point a local dev build at http://localhost:8000,
// or a self-hosted backend at https://api.example.com).
const DEFAULT_API_ORIGIN = "https://ronin-api-hjmv.onrender.com";
const API_ORIGIN = process.env.RONIN_API_ORIGIN || DEFAULT_API_ORIGIN;

const nextConfig = {
  reactStrictMode: true,
  transpilePackages: ["@ronin/design-system"],
  // Same-origin proxy to the hosted backend so the browser never makes a
  // cross-origin request (no CORS needed): /api/v1/* is forwarded to the
  // backend at build time. The OS worlds call same-origin `/api/v1/...` (see
  // lib/api.ts, AIOS_BASE = "") and this rewrite carries them to a live API.
  async rewrites() {
    return [
      { source: "/api/v1/:path*", destination: `${API_ORIGIN}/api/v1/:path*` },
    ];
  },
};

module.exports = nextConfig;

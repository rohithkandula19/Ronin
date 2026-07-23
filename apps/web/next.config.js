/** @type {import('next').NextConfig} */
const nextConfig = {
  reactStrictMode: true,
  transpilePackages: ["@ronin/design-system"],
  // Same-origin proxy to a hosted backend so the browser never makes a
  // cross-origin request (no CORS needed). Set RONIN_API_ORIGIN to the backend
  // base URL (e.g. https://api.example.com). When unset, no proxy is added and
  // the OS worlds fall back to their labelled offline sample.
  async rewrites() {
    const origin = process.env.RONIN_API_ORIGIN;
    return origin
      ? [{ source: "/api/v1/:path*", destination: `${origin}/api/v1/:path*` }]
      : [];
  },
  env: {
    NEXT_PUBLIC_API_URL: process.env.NEXT_PUBLIC_API_URL || "http://localhost:8000",
  },
};

module.exports = nextConfig;

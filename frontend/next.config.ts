import type { NextConfig } from "next";

const nextConfig: NextConfig = {
  // Dev + PoC bind to loopback only (start with -H 127.0.0.1).
  async rewrites() {
    return [
      {
        source: "/api/:path*",
        destination: "http://127.0.0.1:8000/api/:path*",
      },
      {
        source: "/health",
        destination: "http://127.0.0.1:8000/health",
      },
    ];
  },
};

export default nextConfig;

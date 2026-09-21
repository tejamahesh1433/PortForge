import type { NextConfig } from "next";

const nextConfig: NextConfig = {
  // Completely disable ETags to prevent 304 Not Modified responses
  generateEtags: false,
  reactStrictMode: true,
  output: "standalone",

  
  // Force a unique build ID so new builds invalidate the cache
  generateBuildId: async () => {
    return `build-${Date.now()}`;
  },
  
  // Add extreme anti-caching headers for every single route and file
  async headers() {
    return [
      {
        source: "/(.*)",
        headers: [
          {
            key: "Cache-Control",
            value: "no-store, no-cache, must-revalidate, proxy-revalidate",
          },
          {
            key: "Pragma",
            value: "no-cache",
          },
          {
            key: "Expires",
            value: "0",
          }
        ],
      },
    ];
  },
};

export default nextConfig;

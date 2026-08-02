/** @type {import('next').NextConfig} */
const nextConfig = {
  reactStrictMode: true,
  // Ships a self-contained server bundle so the runtime image needs no node_modules.
  output: "standalone",
  async rewrites() {
    return [
      {
        source: "/api/:path*",
        destination: `${process.env.API_BASE_URL ?? "http://localhost:8100"}/api/:path*`,
      },
    ];
  },
};

export default nextConfig;

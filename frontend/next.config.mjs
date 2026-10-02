/** @type {import('next').NextConfig} */
const nextConfig = {
  // Opt-in same-origin API proxy for environments where the browser can't
  // reach the backend port directly (e.g. private Codespaces ports). Set
  // API_PROXY_TARGET=http://localhost:8000 and NEXT_PUBLIC_API_BASE_URL=""
  // so the browser calls relative /api/* paths. Unset = no rewrites.
  async rewrites() {
    const target = process.env.API_PROXY_TARGET;
    return target ? [{ source: "/api/:path*", destination: `${target}/api/:path*` }] : [];
  },
};

export default nextConfig;

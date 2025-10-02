/** @type {import('next').NextConfig} */
const nextConfig = {
  eslint: {
    ignoreDuringBuilds: true,
  },
  typescript: {
    ignoreBuildErrors: true,
  },
  images: {
    unoptimized: true,
  },
  env: {
    NEXT_PUBLIC_API_PORT: process.env.API_PORT || '8001',
    NEXT_PUBLIC_MEDIAMTX_WEBRTC_PORT: process.env.MEDIAMTX_WEBRTC_PORT || '8889',
  },
  async rewrites() {
    const apiPort = process.env.API_PORT || '8001'
    return [
      {
        source: '/api/:path*',
        destination: `http://127.0.0.1:${apiPort}/api/:path*`,
      },
    ]
  },
}

export default nextConfig

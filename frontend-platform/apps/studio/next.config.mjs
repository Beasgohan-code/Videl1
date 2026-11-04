const configured = process.env.VIDEL_BACKEND_ORIGIN;
let backend;
if (configured) {
  const url = new URL(configured);
  if (!['http:', 'https:'].includes(url.protocol) || url.username || url.password || url.pathname !== '/' || url.search || url.hash) throw new Error('VIDEL_BACKEND_ORIGIN must be a plain HTTP(S) origin.');
  backend = url.origin;
}
/** @type {import('next').NextConfig} */
const config = {
  basePath:'/next-preview',
  allowedDevOrigins:['*.e2b.app'],
  async rewrites() {
    if (!backend) return [];
    return [
      {source:'/api/v1/:path*',destination:`${backend}/api/v1/:path*`,basePath:false},
      {source:'/app',destination:`${backend}/app`,basePath:false},
      {source:'/app/:path*',destination:`${backend}/app/:path*`,basePath:false},
    ];
  },
};
export default config;

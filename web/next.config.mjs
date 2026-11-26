// A stray lockfile in the home directory makes Next infer the wrong workspace root, which
// silently changes which files are traced into the build. Pin it to this directory.
//
// The /api rewrite is what makes cookie auth possible at all. The service runs on a Lambda
// function URL, a different origin from this app. A session cookie marked SameSite=Lax is
// not sent on a cross-origin request, so a token held in a cookie would simply never
// arrive. Proxying /api through this origin makes every call first-party: the browser
// sends the cookie, and the token is never visible to page script.
const API = process.env.SETUHAUL_API_ORIGIN
         || process.env.NEXT_PUBLIC_API_URL
         || "http://127.0.0.1:8099";

const nextConfig = {
  reactStrictMode: true,
  outputFileTracingRoot: import.meta.dirname,
  async rewrites() {
    return [{ source: "/api/:path*", destination: `${API}/api/:path*` }];
  },
};
export default nextConfig;

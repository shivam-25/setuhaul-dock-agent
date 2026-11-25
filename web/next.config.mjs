// A stray lockfile in the home directory makes Next infer the wrong workspace root, which
// silently changes which files are traced into the build. Pin it to this directory.
const nextConfig = {
  reactStrictMode: true,
  outputFileTracingRoot: import.meta.dirname,
};
export default nextConfig;

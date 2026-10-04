/** @type {import('next').NextConfig} */
const nextConfig = {
  // Prevent webpack from bundling native Node.js addons and heavy libs
  serverExternalPackages: ['puppeteer', 'puppeteer-core'],
};

module.exports = nextConfig;

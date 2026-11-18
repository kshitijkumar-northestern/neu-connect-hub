// @ts-check
const { createHash } = require("node:crypto");
const fs = require("node:fs");
const path = require("node:path");
const webpack = require("webpack");
const HtmlWebpackPlugin = require("html-webpack-plugin");
const MiniCssExtractPlugin = require("mini-css-extract-plugin");

const envFile = path.join(__dirname, ".env");
if (fs.existsSync(envFile)) process.loadEnvFile(envFile);

// The only values that reach the browser bundle. Never add backend configuration here.
const publicEnv = {
  API_BASE_URL: process.env.API_BASE_URL ?? "/api/v1",
  SOCKET_URL: process.env.SOCKET_URL ?? "",
};

// Dev server proxy target, so the browser talks to one origin.
const devApiOrigin = process.env.DEV_API_ORIGIN ?? "http://localhost:8000";

// An absolute http(s) or ws(s) URL, or a protocol-relative one, with a host.
const ABSOLUTE_URL = /^(?:(?:https?|wss?):)?\/\/[^/?#]/i;

/**
 * connect-src entries for the API and Socket.IO URLs when the API runs on its own host: each
 * origin over both HTTP and WebSocket (socket.io-client starts with either), secure unless the
 * URL itself is plain http:// or ws://, as it is locally. Unset, empty or path values are the
 * page's own origin, which 'self' covers. Anything else fails the build: it would leave the app
 * talking to an origin the policy blocks.
 * @param {Record<string, string | undefined>} urls by variable name
 */
function connectSources(urls) {
  const sources = new Set();
  for (const [name, url] of Object.entries(urls)) {
    if (!url || (url.startsWith("/") && !url.startsWith("//"))) continue;
    if (!ABSOLUTE_URL.test(url)) {
      throw new Error(`${name} must be a path on this site or an absolute http(s):// or ws(s):// URL, not ${JSON.stringify(url)}`);
    }
    const { protocol, host } = new URL(url.startsWith("//") ? `https:${url}` : url);
    const secure = protocol === "https:" || protocol === "wss:";
    sources.add(`${secure ? "https" : "http"}://${host}`);
    sources.add(`${secure ? "wss" : "ws"}://${host}`);
  }
  return [...sources];
}

const THEME_SCRIPT = /<script\b[^>]*\bid=["']?theme-init["']?[^>]*>([\s\S]*?)<\/script>/i;
const CHARSET_META = /<meta\b[^>]*\bcharset=[^>]*>/i;

/**
 * Adds the Content Security Policy to the built page, right after the charset so it governs every
 * script after it. The inline theme script (src/index.html) is allowed by the sha256 of its exact
 * built text, so editing it changes the hash on the next build; scripts/check-csp.mjs re-checks
 * the result after every production build.
 * @param {string} html
 * @param {string[]} extraConnectSources
 */
function injectContentSecurityPolicy(html, extraConnectSources) {
  const theme = html.match(THEME_SCRIPT);
  if (!theme) throw new Error('index.html has no <script id="theme-init"> for the CSP to allow');
  const hash = createHash("sha256").update(theme[1] ?? "", "utf8").digest("base64");
  const policy = [
    "default-src 'self'",
    `script-src 'self' 'sha256-${hash}'`,
    // Radix UI positions popovers, menus and dialogs with inline style attributes, which only
    // 'unsafe-inline' allows. Styles can't run script, and script-src stays locked to hashes.
    "style-src 'self' 'unsafe-inline'",
    "img-src 'self' data: blob:",
    "font-src 'self'",
    "media-src 'self' blob:",
    ["connect-src 'self'", ...extraConnectSources].join(" "),
    "object-src 'none'",
    "base-uri 'self'",
    "form-action 'self'",
    // frame-ancestors is ignored in a meta tag: the host must send it as a header.
  ].join("; ");
  const meta = `<meta http-equiv="Content-Security-Policy" content="${policy}">`;
  if (!CHARSET_META.test(html)) throw new Error("index.html needs a <meta charset> before the CSP");
  return html.replace(CHARSET_META, (charset) => charset + meta);
}

/** Production only: development builds have no policy, because hot reloading needs eval. */
class ContentSecurityPolicyPlugin {
  /** @param {string[]} extraConnectSources */
  constructor(extraConnectSources) {
    this.extraConnectSources = extraConnectSources;
  }

  /** @param {import("webpack").Compiler} compiler */
  apply(compiler) {
    const { Compilation, sources } = compiler.webpack;
    compiler.hooks.thisCompilation.tap("ContentSecurityPolicyPlugin", (compilation) => {
      compilation.hooks.processAssets.tap(
        // After the minimizers, which rewrite the inline script: the hash must be of the bytes
        // that are served, not of what html-webpack-plugin first emits.
        { name: "ContentSecurityPolicyPlugin", stage: Compilation.PROCESS_ASSETS_STAGE_OPTIMIZE_HASH },
        (assets) => {
          for (const name of Object.keys(assets).filter((asset) => asset.endsWith(".html"))) {
            const html = compilation.getAsset(name)?.source.source().toString() ?? "";
            const withPolicy = injectContentSecurityPolicy(html, this.extraConnectSources);
            compilation.updateAsset(name, new sources.RawSource(withPolicy));
          }
        },
      );
    });
  }
}

module.exports = (_env, argv) => {
  const isProd = argv.mode === "production";

  return {
    mode: isProd ? "production" : "development",
    entry: "./src/main.tsx",
    output: {
      path: path.resolve(__dirname, "dist"),
      filename: isProd ? "assets/[name].[contenthash:8].js" : "assets/[name].js",
      chunkFilename: isProd ? "assets/[name].[contenthash:8].js" : "assets/[name].js",
      assetModuleFilename: "assets/[name].[contenthash:8][ext]",
      publicPath: "/",
      clean: true,
    },
    devtool: isProd ? "source-map" : "eval-cheap-module-source-map",
    resolve: {
      extensions: [".tsx", ".ts", ".js"],
      alias: { "@": path.resolve(__dirname, "src") },
    },
    module: {
      rules: [
        {
          test: /\.[jt]sx?$/,
          exclude: /node_modules/,
          use: {
            loader: "swc-loader",
            options: {
              jsc: {
                parser: { syntax: "typescript", tsx: true },
                transform: { react: { runtime: "automatic", development: !isProd } },
                target: "es2022",
              },
            },
          },
        },
        {
          test: /\.css$/,
          use: [
            isProd ? MiniCssExtractPlugin.loader : "style-loader",
            { loader: "css-loader", options: { importLoaders: 1 } },
            "postcss-loader",
          ],
        },
        { test: /\.(png|svg|jpe?g|gif|webp)$/i, type: "asset" },
        // Always files: "asset" would inline the smaller font subsets as data: URIs, which the
        // production CSP's font-src 'self' blocks.
        { test: /\.woff2?$/i, type: "asset/resource" },
      ],
    },
    plugins: [
      new HtmlWebpackPlugin({ template: "./src/index.html", favicon: "./src/assets/brand/favicon.svg" }),
      new webpack.DefinePlugin({
        __API_BASE_URL__: JSON.stringify(publicEnv.API_BASE_URL),
        __SOCKET_URL__: JSON.stringify(publicEnv.SOCKET_URL),
      }),
      ...(isProd
        ? [
            new MiniCssExtractPlugin({ filename: "assets/[name].[contenthash:8].css" }),
            new ContentSecurityPolicyPlugin(connectSources(publicEnv)),
          ]
        : []),
    ],
    devServer: {
      port: 8080,
      historyApiFallback: true,
      hot: true,
      proxy: [{ context: ["/api", "/socket.io"], target: devApiOrigin, ws: true }],
    },
    performance: { hints: isProd ? "warning" : false },
  };
};

// For scripts/check-csp.test.mjs.
module.exports.connectSources = connectSources;
module.exports.injectContentSecurityPolicy = injectContentSecurityPolicy;

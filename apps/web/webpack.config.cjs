// @ts-check
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

/** @type {(env: unknown, argv: { mode?: string }) => import("webpack").Configuration & { devServer?: import("webpack-dev-server").Configuration }} */
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
        { test: /\.(png|svg|jpe?g|gif|webp|woff2?)$/i, type: "asset" },
      ],
    },
    plugins: [
      new HtmlWebpackPlugin({ template: "./src/index.html", favicon: "./src/assets/brand/favicon.svg" }),
      new webpack.DefinePlugin({
        __API_BASE_URL__: JSON.stringify(publicEnv.API_BASE_URL),
        __SOCKET_URL__: JSON.stringify(publicEnv.SOCKET_URL),
      }),
      ...(isProd ? [new MiniCssExtractPlugin({ filename: "assets/[name].[contenthash:8].css" })] : []),
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

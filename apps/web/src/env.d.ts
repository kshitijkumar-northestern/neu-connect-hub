// Stylesheets are imported for their side effects and handled by css-loader / postcss-loader.
declare module "*.css";

// Images are emitted by webpack's asset modules; the import is the file's public URL.
declare module "*.svg" {
  const url: string;
  export default url;
}

// Tests read source files as text through Vite's ?raw imports.
declare module "*?raw" {
  const text: string;
  export default text;
}

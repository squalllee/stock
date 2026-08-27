import fs from "node:fs/promises";
import path from "node:path";
import { fileURLToPath } from "node:url";

import * as esbuild from "esbuild";

const appDirectory = path.dirname(fileURLToPath(import.meta.url));
const watch = process.argv.includes("--watch");
const outputDirectory = path.join(appDirectory, "dist");

await fs.mkdir(path.join(outputDirectory, "assets"), { recursive: true });
await fs.copyFile(
  path.join(appDirectory, "index.html"),
  path.join(outputDirectory, "index.html"),
);

const options = {
  entryPoints: [path.join(appDirectory, "src/main.jsx")],
  outfile: path.join(outputDirectory, "assets/app.js"),
  bundle: true,
  format: "esm",
  jsx: "automatic",
  minify: !watch,
  sourcemap: watch,
  target: ["es2020"],
  loader: { ".js": "jsx", ".jsx": "jsx" },
  logLevel: "info",
};

if (watch) {
  const context = await esbuild.context(options);
  await context.watch();
  console.log("Website assets are being watched for changes.");
  await new Promise(() => {});
} else {
  await esbuild.build(options);
}

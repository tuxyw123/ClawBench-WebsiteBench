import assert from "node:assert/strict";
import { access, readFile, readdir } from "node:fs/promises";
import test from "node:test";

import {
  cacheSeconds,
  canonicalUrl,
  contentTypeForPath,
} from "../src/index.js";

async function htmlFilesUnder(directory) {
  const files = [];
  for (const entry of await readdir(directory, { withFileTypes: true })) {
    const child = new URL(`${entry.name}${entry.isDirectory() ? "/" : ""}`, directory);
    if (entry.isDirectory()) files.push(...(await htmlFilesUnder(child)));
    if (entry.isFile() && entry.name.endsWith(".html")) files.push(child);
  }
  return files;
}

test("maps root and clean viewer paths to the canonical snapshot", () => {
  assert.equal(
    canonicalUrl("https://website-bench.com/").href,
    "https://raw.githubusercontent.com/tuxyw123/ClawBench-WebsiteBench/main/deploy/websitebench-cloudflare-worker/public/index.html",
  );
  assert.equal(
    canonicalUrl("https://website-bench.com/amazon?mode=compare").href,
    "https://raw.githubusercontent.com/tuxyw123/ClawBench-WebsiteBench/main/deploy/websitebench-cloudflare-worker/public/amazon/index.html?mode=compare",
  );
  assert.equal(
    canonicalUrl("https://website-bench.com/static/styles.css?v=1").href,
    "https://raw.githubusercontent.com/tuxyw123/ClawBench-WebsiteBench/main/deploy/websitebench-cloudflare-worker/public/static/styles.css?v=1",
  );
});

test("uses short HTML caching and longer immutable-asset caching", () => {
  assert.equal(cacheSeconds("/amazon", "text/html; charset=utf-8"), 60);
  assert.equal(cacheSeconds("/static/styles.css", "text/css"), 3600);
  assert.equal(contentTypeForPath("/amazon"), "text/html; charset=utf-8");
  assert.equal(
    contentTypeForPath("/data/index.json"),
    "application/json; charset=utf-8",
  );
  assert.equal(contentTypeForPath("/static/source.jpg"), "image/jpeg");
});

test("does not expose a writable API surface", async () => {
  const worker = (await import("../src/index.js")).default;
  const env = {
    ASSETS: {
      fetch: () => {
        throw new Error("API requests must not reach static assets");
      },
    },
  };
  const context = { waitUntil() {} };

  const response = await worker.fetch(
    new Request("https://website-bench.com/api/reviews/example"),
    env,
    context,
  );
  assert.equal(response.status, 404);
});

test("ships a strict-CSP-compatible viewer shell", async () => {
  const publicRoot = new URL("../public/", import.meta.url);
  const htmlFiles = await htmlFilesUnder(publicRoot);
  assert.equal(htmlFiles.length, 9);
  await access(new URL("static/favicon.svg", publicRoot));
  await Promise.all(
    htmlFiles.map(async (file) => {
      const html = await readFile(file, "utf8");
      assert.doesNotMatch(html, /\sstyle=/, file.pathname);
      assert.match(
        html,
        /style-src 'self'; script-src 'self'/,
        file.pathname,
      );
      assert.match(
        html,
        /rel="icon" href="\/static\/favicon\.svg"/,
        file.pathname,
      );
    }),
  );
});

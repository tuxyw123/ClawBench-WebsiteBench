import assert from "node:assert/strict";
import { access, readFile } from "node:fs/promises";
import test from "node:test";

const projectRoot = new URL("../", import.meta.url);
const publicRoot = new URL("../public/", import.meta.url);

async function text(relative) {
  return readFile(new URL(relative, publicRoot), "utf8");
}

test("publishes the reconstruction-first benchmark overview", async () => {
  const html = await text("index.html");
  assert.match(html, /Can an agent rebuild a website it can only/);
  assert.match(html, /Dataset construction · active/);
  assert.match(html, /Agent experiments · not started/);
  assert.match(html, /Amazon Shopping/);
  assert.doesNotMatch(html, /http:\/\/testserver/);
  assert.match(html, /property="og:image" content="\/static\/og-v2\.png"/);
});

test("publishes one real Amazon item and keeps experiment output empty", async () => {
  const [detail, results, data] = await Promise.all([
    text("tasks/offlineclone--amazon-shopping-mainline/index.html"),
    text("results/index.html"),
    text("data/index.json").then(JSON.parse),
  ]);
  assert.equal(data.summary.benchmark_site_count, 1);
  assert.equal(data.summary.official_run_count, 0);
  assert.equal(data.items[0].experiment_status, "not_started");
  assert.match(detail, /Route & state explorer/);
  assert.match(detail, /Journey replay/);
  assert.match(detail, /source → offline reference/);
  assert.match(results, /Experiment not started/);
});

test("includes the sanitized public evidence bundle", async () => {
  for (const relative of [
    "static/og-v2.png",
    "static/showcase/amazon/source-home.png",
    "static/showcase/amazon/clone-home.png",
    "static/showcase/amazon/diff-home.png",
    "static/showcase/amazon/source-search.png",
    "static/showcase/amazon/clone-search.png",
    "static/showcase/amazon/diff-search.png",
  ]) {
    await access(new URL(relative, publicRoot));
  }
  const worker = await readFile(new URL("worker/index.ts", projectRoot), "utf8");
  assert.match(worker, /ASSETS\.fetch/);
  assert.match(worker, /index\.html/);
});

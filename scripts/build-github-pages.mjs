import { cp, readFile, readdir, rm, stat, writeFile } from "node:fs/promises";
import path from "node:path";
import { fileURLToPath } from "node:url";

const repositoryRoot = path.resolve(
  path.dirname(fileURLToPath(import.meta.url)),
  "..",
);
const sourceRoot = path.join(
  repositoryRoot,
  "deploy",
  "websitebench-cloudflare-worker",
  "public",
);
const outputRoot = path.join(repositoryRoot, "dist", "github-pages-viewer");
const configuredBase = process.env.PAGES_BASE_PATH || "/ClawBench-WebsiteBench";
const basePath = `/${configuredBase.replace(/^\/+|\/+$/g, "")}`;
const viewerRoots =
  "(?:amazon|compare|data|methodology|models|results|static|tasks)";

function prefixRootPath(value) {
  if (!value.startsWith("/") || value.startsWith("//")) return value;
  if (value === basePath || value.startsWith(`${basePath}/`)) return value;
  return `${basePath}${value}`;
}

function rewriteHtml(value) {
  return value.replace(
    /(\b(?:action|content|href|src)=["'])(\/(?!\/))/g,
    (_match, opening, slash) => `${opening}${basePath}${slash}`,
  );
}

function rewriteJavaScript(value) {
  const rootPath = new RegExp(
    `([\\\`"'])/(?!/)(?=${viewerRoots}(?:[/?#\\\`"']))`,
    "g",
  );
  return value.replace(rootPath, `$1${basePath}/`);
}

function rewriteCss(value) {
  return value.replace(
    /(\burl\(\s*["']?)(\/(?!\/))/g,
    (_match, opening, slash) => `${opening}${basePath}${slash}`,
  );
}

function rewriteJsonValue(value, key = "") {
  if (Array.isArray(value)) {
    return value.map((item) => rewriteJsonValue(item, key));
  }
  if (value && typeof value === "object") {
    return Object.fromEntries(
      Object.entries(value).map(([childKey, childValue]) => [
        childKey,
        rewriteJsonValue(childValue, childKey),
      ]),
    );
  }
  if (
    typeof value === "string" &&
    /(?:_url|_image|href|src)$/.test(key) &&
    value.startsWith("/")
  ) {
    return prefixRootPath(value);
  }
  return value;
}

async function filesUnder(directory) {
  const files = [];
  for (const entry of await readdir(directory, { withFileTypes: true })) {
    const absolute = path.join(directory, entry.name);
    if (entry.isDirectory()) {
      files.push(...(await filesUnder(absolute)));
    } else if (entry.isFile()) {
      files.push(absolute);
    }
  }
  return files;
}

function outputPathForUrl(reference) {
  const withoutBase = reference.slice(basePath.length).split(/[?#]/, 1)[0];
  const relative = withoutBase.replace(/^\/+/, "");
  if (!relative) return path.join(outputRoot, "index.html");
  if (path.extname(relative)) return path.join(outputRoot, relative);
  return path.join(outputRoot, relative, "index.html");
}

async function validateOutput(files) {
  const unresolved = [];
  const missing = [];
  const localReferences = new Set();

  for (const file of files) {
    const extension = path.extname(file);
    if (![".css", ".html", ".js", ".json"].includes(extension)) continue;
    const value = await readFile(file, "utf8");

    if (extension === ".html") {
      for (const match of value.matchAll(
        /\b(?:action|content|href|src)=["'](\/(?!\/)[^"']*)["']/g,
      )) {
        const reference = match[1];
        if (!reference.startsWith(`${basePath}/`) && reference !== basePath) {
          unresolved.push(`${path.relative(outputRoot, file)}: ${reference}`);
        } else {
          localReferences.add(reference);
        }
      }
    }

    if (extension === ".js") {
      const rootPath = new RegExp(
        `[\\\`"']/(?!/)(?=${viewerRoots}(?:[/?#\\\`"']))`,
      );
      if (rootPath.test(value)) {
        unresolved.push(`${path.relative(outputRoot, file)}: JavaScript root path`);
      }
    }

    if (extension === ".json") {
      const parsed = JSON.parse(value);
      const inspect = (item, key = "") => {
        if (Array.isArray(item)) return item.forEach((child) => inspect(child, key));
        if (item && typeof item === "object") {
          return Object.entries(item).forEach(([childKey, child]) =>
            inspect(child, childKey),
          );
        }
        if (
          typeof item === "string" &&
          /(?:_url|_image|href|src)$/.test(key) &&
          item.startsWith("/") &&
          !item.startsWith(`${basePath}/`)
        ) {
          unresolved.push(`${path.relative(outputRoot, file)}: ${key}=${item}`);
        }
      };
      inspect(parsed);
    }
  }

  for (const reference of localReferences) {
    const target = outputPathForUrl(reference);
    try {
      const targetStat = await stat(target);
      if (!targetStat.isFile()) missing.push(reference);
    } catch {
      missing.push(reference);
    }
  }

  if (unresolved.length || missing.length) {
    throw new Error(
      [
        ...unresolved.map((item) => `unresolved: ${item}`),
        ...missing.map((item) => `missing: ${item}`),
      ].join("\n"),
    );
  }

  return localReferences.size;
}

await rm(outputRoot, { force: true, recursive: true });
await cp(sourceRoot, outputRoot, { recursive: true });

let files = await filesUnder(outputRoot);
for (const file of files) {
  const extension = path.extname(file);
  if (extension === ".html") {
    await writeFile(file, rewriteHtml(await readFile(file, "utf8")));
  } else if (extension === ".js") {
    await writeFile(file, rewriteJavaScript(await readFile(file, "utf8")));
  } else if (extension === ".css") {
    await writeFile(file, rewriteCss(await readFile(file, "utf8")));
  } else if (extension === ".json") {
    const parsed = JSON.parse(await readFile(file, "utf8"));
    if (path.basename(file) === "site-manifest.json") {
      parsed.base_path = `${basePath}/`;
    }
    await writeFile(
      file,
      `${JSON.stringify(rewriteJsonValue(parsed), null, 2)}\n`,
    );
  }
}

files = await filesUnder(outputRoot);
const referenceCount = await validateOutput(files);
console.log(
  `Prepared ${files.length} files for ${basePath}/ with ${referenceCount} verified local references.`,
);

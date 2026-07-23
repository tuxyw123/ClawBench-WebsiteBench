const CANONICAL_RAW_ROOT =
  "https://raw.githubusercontent.com/tuxyw123/ClawBench-WebsiteBench/main/deploy/websitebench-cloudflare-worker/public";
const UPSTREAM_TIMEOUT_MS = 3000;

function canonicalAssetPath(pathname) {
  if (pathname.endsWith("/")) {
    return `${pathname}index.html`;
  }
  const leaf = pathname.split("/").at(-1) || "";
  return leaf.includes(".") ? pathname : `${pathname}/index.html`;
}

export function canonicalUrl(requestUrl) {
  const incoming = new URL(requestUrl);
  const upstream = new URL(`${CANONICAL_RAW_ROOT}/`);
  const pathname = incoming.pathname.startsWith("/")
    ? incoming.pathname
    : `/${incoming.pathname}`;
  upstream.pathname =
    `${upstream.pathname.replace(/\/$/, "")}${canonicalAssetPath(pathname)}`;
  upstream.search = incoming.search;
  return upstream;
}

export function contentTypeForPath(pathname) {
  const effectivePath = canonicalAssetPath(pathname).toLowerCase();
  const types = {
    ".css": "text/css; charset=utf-8",
    ".gif": "image/gif",
    ".html": "text/html; charset=utf-8",
    ".ico": "image/x-icon",
    ".jpeg": "image/jpeg",
    ".jpg": "image/jpeg",
    ".js": "text/javascript; charset=utf-8",
    ".json": "application/json; charset=utf-8",
    ".png": "image/png",
    ".svg": "image/svg+xml",
    ".webp": "image/webp",
    ".woff": "font/woff",
    ".woff2": "font/woff2",
  };
  const extension = Object.keys(types).find((item) =>
    effectivePath.endsWith(item),
  );
  return extension ? types[extension] : "";
}

export function cacheSeconds(pathname, contentType = "") {
  if (
    contentType.includes("text/html") ||
    pathname === "/" ||
    !pathname.split("/").at(-1)?.includes(".")
  ) {
    return 60;
  }
  return 3600;
}

function withPublicHeaders(response, pathname) {
  const headers = new Headers(response.headers);
  const contentType =
    contentTypeForPath(pathname) || headers.get("content-type") || "";
  const ttl = cacheSeconds(pathname, contentType);
  headers.set("Cache-Control", `public, max-age=${ttl}`);
  if (contentType) {
    headers.set("Content-Type", contentType);
  }
  headers.set("Referrer-Policy", "strict-origin-when-cross-origin");
  headers.set("X-Content-Type-Options", "nosniff");
  headers.set("X-Frame-Options", "DENY");
  headers.delete("Set-Cookie");
  return new Response(response.body, {
    status: response.status,
    statusText: response.statusText,
    headers,
  });
}

async function bundledFallback(request, env) {
  const response = await env.ASSETS.fetch(request);
  return withPublicHeaders(response, new URL(request.url).pathname);
}

export default {
  async fetch(request, env, context) {
    if (request.method !== "GET" && request.method !== "HEAD") {
      return new Response("Method Not Allowed", {
        status: 405,
        headers: { Allow: "GET, HEAD" },
      });
    }

    if (new URL(request.url).pathname.startsWith("/api/")) {
      return new Response("Not Found", { status: 404 });
    }

    const cache = caches.default;
    const cached = await cache.match(request);
    if (cached) {
      return cached;
    }

    try {
      const upstreamRequest = new Request(canonicalUrl(request.url), {
        method: request.method,
        headers: {
          Accept: request.headers.get("Accept") || "*/*",
          "User-Agent": "WebsiteBench-Viewer-Worker/2.0",
        },
        redirect: "follow",
        signal: AbortSignal.timeout(UPSTREAM_TIMEOUT_MS),
      });
      const upstream = await fetch(upstreamRequest);
      if (upstream.ok) {
        const response = withPublicHeaders(
          upstream,
          new URL(request.url).pathname,
        );
        if (request.method === "GET") {
          context.waitUntil(cache.put(request, response.clone()));
        }
        return response;
      }
    } catch {
      // The bundled snapshot keeps the domain available if the canonical
      // public snapshot is temporarily unreachable.
    }

    return bundledFallback(request, env);
  },
};

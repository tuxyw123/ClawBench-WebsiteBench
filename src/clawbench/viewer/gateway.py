"""URL, cookie, and response-body rewriting for authenticated clone previews."""

from __future__ import annotations

import json
import re
from typing import Any
from urllib.parse import quote, unquote, urlsplit


CLONE_GATEWAY_ROOT = "/clone"
HOP_BY_HOP_HEADERS = {
    "connection", "keep-alive", "proxy-authenticate", "proxy-authorization",
    "te", "trailer", "transfer-encoding", "upgrade",
}
ROOT_ATTRIBUTE_RE = re.compile(
    r"""(?P<before>\b(?:href|src|action|formaction|poster|data-src)\s*=\s*)(?P<quote>["'])(?P<path>/(?!/))""",
    re.IGNORECASE,
)
ROOT_SRCSET_RE = re.compile(r"""(?P<before>(?:^|,\s*))(?P<path>/(?!/))""")
CSS_ROOT_URL_RE = re.compile(r"""(?P<before>\burl\(\s*["']?)(?P<path>/(?!/))""", re.IGNORECASE)
META_REFRESH_RE = re.compile(r"""(?P<before>\burl\s*=\s*)(?P<path>/(?!/))""", re.IGNORECASE)
CURRENT_URL_RE = re.compile(r"""new\s+URL\(\s*(?:window\.)?location\.href\s*\)""")
WINDOW_PATHNAME_RE = re.compile(r"""\bwindow\.location\.pathname\b""")
BARE_PATHNAME_RE = re.compile(r"""(?<![\w.])location\.pathname\b""")
LOCATION_CALL_RE = re.compile(r"""(?<![\w.])(?:(?:window\.)?location)\.(?P<method>assign|replace)\s*\(""")
LOCATION_HREF_ASSIGN_RE = re.compile(r"""(?<![\w.])(?:(?:window\.)?location)\.href\s*=\s*(?P<value>[^;\n]+)""")
LOOPBACK_URL_RE = re.compile(r"""https?://(?:127\.0\.0\.1|localhost):\d+""", re.IGNORECASE)
LOCAL_ASSET_PATH_RE = re.compile(r"""\.(?:avif|css|gif|ico|jpe?g|js|mjs|png|svg|webp|woff2?)(?:[?#].*)?$""", re.IGNORECASE)
JS_ROOT_ASSET_RE = re.compile(
    r"""(?P<quote>["'])(?P<path>/(?!/)[^"'\r\n]+?\.(?:avif|css|gif|ico|jpe?g|js|mjs|png|svg|webp|woff2?)(?:[?#][^"'\r\n]*)?)(?P=quote)""",
    re.IGNORECASE,
)


def clone_gateway_prefix(item_key: str) -> str:
    return f"{CLONE_GATEWAY_ROOT}/{quote(item_key, safe='')}"


def clone_public_path(item_key: str, clone_path: str = "/") -> str:
    path = clone_path if clone_path.startswith("/") else f"/{clone_path}"
    return f"{clone_gateway_prefix(item_key)}{path}"


def parse_clone_request(raw_target: str) -> tuple[str, str] | None:
    parsed = urlsplit(raw_target)
    if not parsed.path.startswith(f"{CLONE_GATEWAY_ROOT}/"):
        return None
    remainder = parsed.path.removeprefix(f"{CLONE_GATEWAY_ROOT}/")
    encoded_key, separator, tail = remainder.partition("/")
    if not encoded_key:
        return None
    backend_path = f"/{tail}" if separator else "/"
    if parsed.query:
        backend_path += f"?{parsed.query}"
    return unquote(encoded_key), backend_path


def _runtime_script(item_key: str, nonce: str | None = None) -> str:
    prefix = json.dumps(clone_gateway_prefix(item_key))
    nonce_attribute = f' nonce="{nonce}"' if nonce else ""
    return f"""<script{nonce_attribute}>(() => {{
const prefix={prefix}; if(window.__clawbenchPrefix===prefix)return;
window.__clawbenchPrefix = prefix;
const publicPath=(value)=>{{if(value==null||typeof value!=="string")return value;
if(value===prefix||value.startsWith(prefix+"/")||value.startsWith("//"))return value;
return value.startsWith("/")?prefix+value:value;}};
window.__clawbenchPathname=()=>location.pathname.startsWith(prefix)?location.pathname.slice(prefix.length)||"/":location.pathname;
window.__clawbenchCurrentUrl=()=>{{const value=new URL(location.href);value.pathname=window.__clawbenchPathname();return value;}};
window.__clawbenchNavigate=(value,replace=false)=>location[replace?"replace":"assign"](publicPath(value));
window.__clawbenchReplace=(value)=>window.__clawbenchNavigate(value,true);
const nativeFetch=window.fetch.bind(window);window.fetch = (input, init)=>nativeFetch(typeof input==="string"?publicPath(input):input,init);
const push=history.pushState.bind(history),replace=history.replaceState.bind(history);
history.pushState=(state,title,url)=>push(state,title,publicPath(url));history.replaceState=(state,title,url)=>replace(state,title,publicPath(url));
const raw=Element.prototype.getAttribute;Element.prototype.getAttribute = function(name){{const value=raw.call(this,name);return typeof value==="string"&&value.startsWith(prefix+"/")?value.slice(prefix.length):value;}};
const rewrite=(root)=>{{for(const element of [root,...(root.querySelectorAll?.("[href],[src],[action],[formaction],[poster],[data-src]")||[])]){{
for(const name of ["href","src","action","formaction","poster","data-src"]){{const value=raw.call(element,name);if(value?.startsWith("/")&&!value.startsWith("//")&&!value.startsWith(prefix+"/"))element.setAttribute(name,prefix+value);}}}}}};
rewrite(document.documentElement);new MutationObserver(records=>records.forEach(record=>record.addedNodes.forEach(node=>node instanceof Element&&rewrite(node)))).observe(document.documentElement,{{subtree:true,childList:true}});
}})();</script>"""


def _rewrite_javascript(text: str, prefix: str) -> str:
    rewritten = CURRENT_URL_RE.sub("window.__clawbenchCurrentUrl()", text)
    rewritten = WINDOW_PATHNAME_RE.sub("window.__clawbenchPathname()", rewritten)
    rewritten = BARE_PATHNAME_RE.sub("window.__clawbenchPathname()", rewritten)
    rewritten = LOCATION_CALL_RE.sub(
        lambda match: "window.__clawbenchNavigate(" if match.group("method") == "assign" else "window.__clawbenchReplace(",
        rewritten,
    )
    rewritten = LOCATION_HREF_ASSIGN_RE.sub(
        lambda match: f"window.__clawbenchNavigate({match.group('value')})", rewritten
    )

    def asset(match: re.Match[str]) -> str:
        path = match.group("path")
        if path == prefix or path.startswith(prefix + "/"):
            return match.group(0)
        return f"{match.group('quote')}{prefix}{path}{match.group('quote')}"

    return JS_ROOT_ASSET_RE.sub(asset, rewritten)


def _rewrite_html(text: str, item_key: str, nonce: str | None = None) -> str:
    prefix = clone_gateway_prefix(item_key)
    rewritten = LOOPBACK_URL_RE.sub(prefix, text)
    rewritten = ROOT_ATTRIBUTE_RE.sub(
        lambda match: f"{match.group('before')}{match.group('quote')}{prefix}{match.group('path')}",
        rewritten,
    )
    rewritten = re.sub(
        r"""(?P<start>\bsrcset\s*=\s*["'])(?P<value>[^"']*)(?P<end>["'])""",
        lambda match: f"{match.group('start')}{ROOT_SRCSET_RE.sub(lambda part: part.group('before') + prefix + part.group('path'), match.group('value'))}{match.group('end')}",
        rewritten,
        flags=re.IGNORECASE,
    )
    rewritten = CSS_ROOT_URL_RE.sub(lambda match: match.group("before") + prefix + match.group("path"), rewritten)
    rewritten = META_REFRESH_RE.sub(lambda match: match.group("before") + prefix + match.group("path"), rewritten)
    rewritten = _rewrite_javascript(rewritten, prefix)
    if nonce:
        rewritten = re.sub(
            r"<script(?![^>]*\bnonce=)", f'<script nonce="{nonce}"', rewritten,
            flags=re.IGNORECASE,
        )
    script = _runtime_script(item_key, nonce)
    head = re.search(r"<head(?:\s[^>]*)?>", rewritten, re.IGNORECASE)
    return f"{rewritten[:head.end()]}{script}{rewritten[head.end():]}" if head else script + rewritten


def _rewrite_json_assets(value: Any, prefix: str) -> Any:
    if isinstance(value, dict):
        return {key: _rewrite_json_assets(child, prefix) for key, child in value.items()}
    if isinstance(value, list):
        return [_rewrite_json_assets(child, prefix) for child in value]
    if isinstance(value, str) and value.startswith("/") and not value.startswith("//") and LOCAL_ASSET_PATH_RE.search(value):
        return value if value.startswith(prefix + "/") else prefix + value
    return value


def rewrite_clone_body(
    body: bytes,
    content_type: str | None,
    item_key: str,
    *,
    script_nonce: str | None = None,
) -> bytes:
    if not body or not content_type:
        return body
    media_type = content_type.partition(";")[0].strip().lower()
    match = re.search(r"charset\s*=\s*[\"']?([^;\"'\s]+)", content_type, re.IGNORECASE)
    charset = match.group(1) if match else "utf-8"
    try:
        text = body.decode(charset)
    except (LookupError, UnicodeDecodeError):
        return body
    prefix = clone_gateway_prefix(item_key)
    if media_type in {"text/html", "application/xhtml+xml"}:
        text = _rewrite_html(text, item_key, script_nonce)
    elif media_type in {"text/javascript", "application/javascript", "application/x-javascript"}:
        text = LOOPBACK_URL_RE.sub(prefix, _rewrite_javascript(text, prefix))
    elif media_type == "text/css":
        text = LOOPBACK_URL_RE.sub(prefix, text)
        text = CSS_ROOT_URL_RE.sub(lambda item: item.group("before") + prefix + item.group("path"), text)
    elif media_type == "application/json" or media_type.endswith("+json"):
        try:
            text = json.dumps(_rewrite_json_assets(json.loads(text), prefix), ensure_ascii=False, separators=(",", ":"))
        except json.JSONDecodeError:
            return body
    else:
        return body
    try:
        return text.encode(charset)
    except LookupError:
        return body


def rewrite_location(location: str, item_key: str) -> str:
    prefix = clone_gateway_prefix(item_key)
    if location.startswith("/") and not location.startswith("//"):
        return location if location == prefix or location.startswith(prefix + "/") else prefix + location
    parsed = urlsplit(location)
    if parsed.hostname in {"127.0.0.1", "localhost"}:
        path = parsed.path or "/"
        if parsed.query:
            path += f"?{parsed.query}"
        if parsed.fragment:
            path += f"#{parsed.fragment}"
        return prefix + path
    return location


def rewrite_set_cookie(cookie: str, item_key: str) -> str:
    path = f"{clone_gateway_prefix(item_key)}/"
    cookie = re.sub(
        r"(?:^|;\s*)Domain=[^;]*",
        "",
        cookie,
        flags=re.IGNORECASE,
    ).strip("; ")
    if re.search(r"(?:^|;\s*)Path=", cookie, re.IGNORECASE):
        return re.sub(r"((?:^|;\s*)Path=)[^;]*", lambda match: match.group(1) + path, cookie, count=1, flags=re.IGNORECASE)
    return f"{cookie}; Path={path}"

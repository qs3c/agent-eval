"""Audited compatibility fixes for the pinned DSH runtime, applied during packaging."""

import hashlib
from pathlib import Path

SEARCH_MODULE = "node_modules/@deepseek-ai/dsh-web-search-deepseek/lib/index.js"
SEARCH_MODULE_SHA256 = "77ee0524e540a798fab819e96cf7b12135d23869f0d8ecd08c92801f6c763513"
SEARCH_RETURN = "\treturn {\n\t\tsources,\n\t\ttruncated: false\n\t};"
SEARCH_RETURN_FIXED = """\treturn {
\t\tsources,
\t\t// Preserve the provider's answer even when it supplies no citation excerpts.
\t\t// Text remains external data; it must not be invented as a per-source snippet.
\t\tcontent: blocks.filter(block => block.type === "text" && typeof block.text === "string")
\t\t\t.map(block => block.text).filter(text => text.trim().length > 0).join("\\n\\n"),
\t\ttruncated: false
\t};"""


def patched_search_module(source: bytes) -> tuple[bytes, dict]:
    before = hashlib.sha256(source).hexdigest()
    if before != SEARCH_MODULE_SHA256:
        raise ValueError("DSH search module changed; review compatibility patch before packaging")
    text = source.decode()
    if text.count(SEARCH_RETURN) != 1:
        raise ValueError("DSH search response mapping is ambiguous")
    patched = text.replace(SEARCH_RETURN, SEARCH_RETURN_FIXED).encode()
    return patched, {
        "id": "deepseek-search-response-content-v1",
        "path": SEARCH_MODULE,
        "upstream_sha256": before,
        "patched_sha256": hashlib.sha256(patched).hexdigest(),
        "reason": "保留搜索模型 text 正文；不把未归属的正文伪装成来源引用摘要",
    }


def patched_fetch_modules(runtime: Path):
    patches = [
        ("dsh-tool-web", "a8313d4e02c751f79d0f91dceb8dfcaa29e7c7a0299ab0d7f8db65471217b0c8",
         "\t\t\tconst result = await ctx.web.fetch({ url: input.url }, exec.signal);",
         "\t\t\tconst result = await ctx.web.fetch({ url: input.url }, exec.signal);\n"
         "\t\t\tawait reviewFetch('content', result.url, formatFetchOutput(result, maxOutputChars), exec.signal);"),
    ]
    files, audits = {}, []
    helper = (Path(__file__).parent / "resources/dsh-fetch-review.mjs").read_bytes()
    for package, expected, before, after in patches:
        path = f"node_modules/@deepseek-ai/{package}/lib/index.js"
        source = (runtime / path).read_bytes()
        if hashlib.sha256(source).hexdigest() != expected or source.decode().count(before) != 1:
            raise ValueError(f"DSH {package} changed; review fetch compatibility patch")
        patched = ("import { reviewFetch } from './eval-fetch-review.mjs';\n" + source.decode().replace(before, after)).encode()
        files[path] = patched
        helper_path = str(Path(path).with_name("eval-fetch-review.mjs"))
        files[helper_path] = helper
        audits.append({"id": "web-fetch-content-review-v2", "path": path, "upstream_sha256": expected,
                       "patched_sha256": hashlib.sha256(patched).hexdigest(),
                       "helper_path": helper_path, "helper_sha256": hashlib.sha256(helper).hexdigest()})
    return files, audits

"""Web 搜索工具：搜索互联网、读取网页内容。

优先使用 Tavily（需 API Key），降级到百度搜索。
"""

import json
import logging
import os
import re

from tools.registry import registry, tool_error, tool_result

logger = logging.getLogger(__name__)

_MAX_CONTENT_LENGTH = 50000


# ---------------------------------------------------------------------------
# web_search
# ---------------------------------------------------------------------------

def _web_search(query: str, max_results: int = 5) -> str:
    api_key = os.getenv("TAVILY_API_KEY")
    if api_key:
        try:
            return _search_tavily(query, max_results, api_key)
        except Exception as e:
            logger.warning("Tavily 搜索失败，降级到百度: %s: %s", type(e).__name__, e)
    try:
        return _search_baidu(query, max_results)
    except Exception as e:
        return tool_error(f"{type(e).__name__}: {e}")


def _search_tavily(query: str, max_results: int, api_key: str) -> str:
    from tavily import TavilyClient
    client = TavilyClient(api_key=api_key)
    response = client.search(query, max_results=max_results)

    results = []
    for item in response.get("results", []):
        results.append({
            "title": item.get("title", ""),
            "url": item.get("url", ""),
            "snippet": item.get("content", "")[:500],
        })

    return tool_result(
        success=True,
        query=query,
        engine="tavily",
        results=results,
    )


def _search_baidu(query: str, max_results: int) -> str:
    import httpx

    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                       "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
    }

    with httpx.Client(follow_redirects=True, timeout=15.0) as client:
        resp = client.get(
            "https://www.baidu.com/s",
            params={"wd": query, "rn": str(max_results)},
            headers=headers,
        )
        resp.raise_for_status()

    html = resp.text
    results = _parse_baidu_results(html, max_results)

    return tool_result(
        success=True,
        query=query,
        engine="baidu",
        results=results,
    )


def _parse_baidu_results(html: str, max_results: int) -> list:
    """从百度搜索结果页提取标题、链接和摘要。"""
    results = []
    # 百度结果容器
    blocks = re.findall(
        r'<div[^>]*class="[^"]*c-container[^"]*"[^>]*>(.*?)</div>\s*</div>\s*</div>',
        html, re.DOTALL,
    )

    for block in blocks[:max_results]:
        title_match = re.search(r'<a[^>]*href="([^"]*)"[^>]*>(.*?)</a>', block, re.DOTALL)
        if not title_match:
            continue

        url = title_match.group(1)
        title = _strip_html(title_match.group(2))

        # 提取摘要
        snippet = ""
        snippet_match = re.search(
            r'<span[^>]*class="[^"]*content-right_[^"]*"[^>]*>(.*?)</span>',
            block, re.DOTALL,
        )
        if snippet_match:
            snippet = _strip_html(snippet_match.group(1))
        if not snippet:
            # 备选：提取第二个 span
            spans = re.findall(r'<span[^>]*>(.*?)</span>', block, re.DOTALL)
            for span in spans:
                text = _strip_html(span)
                if len(text) > 30:
                    snippet = text
                    break

        if title:
            results.append({
                "title": title[:200],
                "url": url,
                "snippet": snippet[:500],
            })

    return results


def _strip_html(text: str) -> str:
    """移除 HTML 标签，清理空白。"""
    text = re.sub(r'<[^>]+>', '', text)
    text = re.sub(r'&[a-zA-Z]+;', ' ', text)
    text = re.sub(r'\s+', ' ', text).strip()
    return text


registry.register(
    name="web_search",
    toolset="web",
    schema={
        "name": "web_search",
        "description": "搜索互联网获取最新信息。返回搜索结果列表（标题、链接、摘要）。",
        "parameters": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "搜索关键词"},
                "max_results": {"type": "integer", "description": "最大返回结果数，默认5"},
            },
            "required": ["query"],
        },
    },
    handler=lambda args, **kw: _web_search(
        query=args["query"],
        max_results=args.get("max_results", 5),
    ),
    check_fn=lambda: True,
)


# ---------------------------------------------------------------------------
# web_read
# ---------------------------------------------------------------------------

def _web_read(url: str) -> str:
    try:
        import httpx

        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                           "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
            "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
        }

        with httpx.Client(follow_redirects=True, timeout=20.0) as client:
            resp = client.get(url, headers=headers)
            resp.raise_for_status()

        content_type = resp.headers.get("content-type", "")
        if "text/html" in content_type:
            text = _extract_text_from_html(resp.text)
        else:
            text = resp.text

        if len(text) > _MAX_CONTENT_LENGTH:
            text = text[:_MAX_CONTENT_LENGTH] + f"\n\n[已截断: 原文{len(text)}字]"

        return tool_result(
            success=True,
            url=url,
            content=text,
        )
    except Exception as e:
        return tool_error(f"{type(e).__name__}: {e}")


def _extract_text_from_html(html: str) -> str:
    """从 HTML 提取正文文本，尝试保留结构。"""
    # 移除 script、style、nav、footer 等
    html = re.sub(r'<(script|style|nav|footer|header|aside)[^>]*>.*?</\1>', '', html, flags=re.DOTALL)
    html = re.sub(r'<!--.*?-->', '', html, flags=re.DOTALL)

    # 将块级标签转为换行
    html = re.sub(r'<(br|p|div|h[1-6]|li|tr)[^>]*>', '\n', html)
    html = re.sub(r'</(p|div|h[1-6]|li|tr)>', '\n', html)

    text = _strip_html(html)
    # 清理多余空行
    text = re.sub(r'\n{3,}', '\n\n', text)
    return text.strip()


registry.register(
    name="web_read",
    toolset="web",
    schema={
        "name": "web_read",
        "description": "读取网页内容，返回正文文本。用于获取搜索结果中某个链接的详细内容。",
        "parameters": {
            "type": "object",
            "properties": {
                "url": {"type": "string", "description": "要读取的网页 URL"},
            },
            "required": ["url"],
        },
    },
    handler=lambda args, **kw: _web_read(url=args["url"]),
    check_fn=lambda: True,
)

"""
Graph builder - renders an interactive OSINT graph and exports PNG previews.
"""

import html as html_module
import math
import os
import uuid
from pathlib import Path
from typing import Optional

from pyvis.network import Network

from config import config
from entity_extractor import ENTITY_META, Entity, Link, extract_entities


_NETWORK_OPTIONS = """
var options = {
  "nodes": {
    "shape": "dot",
    "borderWidth": 2,
    "shadow": {
      "enabled": true,
      "color": "rgba(57,255,20,0.18)",
      "size": 14,
      "x": 0,
      "y": 0
    },
    "font": {
      "color": "#b8ffcc",
      "face": "JetBrains Mono, Consolas, monospace",
      "size": 16,
      "strokeWidth": 3,
      "strokeColor": "#060b0d"
    }
  },
  "edges": {
    "smooth": { "type": "dynamic" },
    "font": {
      "color": "#7fffd4",
      "face": "JetBrains Mono, Consolas, monospace",
      "size": 11,
      "strokeWidth": 2,
      "strokeColor": "#060b0d"
    }
  },
  "interaction": {
    "hover": true,
    "navigationButtons": true,
    "keyboard": true
  },
  "physics": {
    "enabled": true,
    "solver": "forceAtlas2Based",
    "forceAtlas2Based": {
      "gravitationalConstant": -55,
      "centralGravity": 0.015,
      "springLength": 180,
      "springConstant": 0.05,
      "damping": 0.68,
      "avoidOverlap": 1
    },
    "stabilization": {
      "enabled": true,
      "iterations": 250,
      "fit": true
    }
  }
}
"""


def _node_color(entity_type: str) -> str:
    return ENTITY_META.get(entity_type, {}).get("color", "#94a3b8")


def _node_icon(entity_type: str) -> str:
    return ENTITY_META.get(entity_type, {}).get("icon", "•")


def _edge_style(confidence: float) -> dict:
    if confidence >= config.STRONG_LINK_THRESHOLD:
        return {"color": "#39ff14", "width": 3, "dashes": False}
    if confidence >= config.MEDIUM_LINK_THRESHOLD:
        return {"color": "#20c997", "width": 2, "dashes": False}
    return {"color": "#2f6b4a", "width": 1, "dashes": True}


def _create_network() -> Network:
    net = Network(
        height="100vh",
        width="100%",
        bgcolor="#060b0d",
        font_color="#b8ffcc",
        directed=False,
        cdn_resources="in_line",
    )
    net.set_options(_NETWORK_OPTIONS)
    return net


def _inject_theme(html: str, hud_label: str) -> str:
    safe_label = html_module.escape(hud_label)
    extra = f"""
<style>
  body {{
    margin: 0;
    background: #060b0d;
    color: #b8ffcc;
    font-family: 'JetBrains Mono', Consolas, monospace;
  }}
  #mynetwork {{
    width: 100% !important;
    height: 100vh !important;
    background:
      linear-gradient(rgba(57,255,20,0.03) 1px, transparent 1px),
      linear-gradient(90deg, rgba(57,255,20,0.03) 1px, transparent 1px),
      #060b0d !important;
    background-size: 40px 40px, 40px 40px, auto !important;
    border: none !important;
  }}
  .vis-tooltip {{
    background: rgba(11,20,24,0.96) !important;
    border: 1px solid #1a3a30 !important;
    color: #b8ffcc !important;
    border-radius: 6px !important;
    padding: 10px 12px !important;
    box-shadow: 0 0 24px rgba(57,255,20,0.12) !important;
    max-width: 320px !important;
  }}
  .hud {{
    position: fixed;
    top: 14px;
    left: 18px;
    z-index: 20;
    color: #39ff14;
    font-size: 12px;
    letter-spacing: 2px;
    text-transform: uppercase;
    text-shadow: 0 0 8px rgba(57,255,20,0.25);
    pointer-events: none;
  }}
</style>
<script>
  window.addEventListener('load', () => {{
    const hud = document.createElement('div');
    hud.className = 'hud';
    hud.textContent = '{safe_label}';
    document.body.appendChild(hud);

    const refit = () => {{
      if (typeof network !== 'undefined') {{
        network.fit({{ animation: false }});
      }}
    }};

    setTimeout(refit, 500);
    setTimeout(refit, 1800);
    window.__overnettingFit = refit;
  }});
</script>
"""
    return html.replace("</head>", extra + "\n</head>", 1)


def _save_network_html(
    net: Network,
    filename_prefix: str,
    investigation_id: int,
    hud_label: str,
) -> str:
    os.makedirs(config.GRAPH_OUTPUT_DIR, exist_ok=True)
    out_path = os.path.join(
        config.GRAPH_OUTPUT_DIR,
        f"{filename_prefix}_{investigation_id}_{uuid.uuid4().hex[:8]}.html",
    )
    html = net.generate_html(name=f"{filename_prefix}_{investigation_id}.html", local=True, notebook=False)
    html = _inject_theme(html, hud_label)
    Path(out_path).write_text(html, encoding="utf-8")
    return out_path


def _normalize_scores(matches: list[dict]) -> list[float]:
    scores = [float(m.get("score") or 0) for m in matches]
    max_score = max(scores) if scores else 1.0
    if max_score <= 0:
        return [0.5] * len(matches)
    return [min(1.0, s / max_score) for s in scores]


def _truncate(text: str, limit: int) -> str:
    text = (text or "").strip()
    if len(text) <= limit:
        return text
    return text[: limit - 1] + "…"


def build_graph_html(
    entities: list[Entity],
    links: list[Link],
    investigation_id: int,
) -> str:
    degree: dict[str, int] = {}
    for link in links:
        degree[link.a] = degree.get(link.a, 0) + 1
        degree[link.b] = degree.get(link.b, 0) + 1

    net = _create_network()
    entity_map: dict[str, Entity] = {}
    node_count = max(len(entities), 1)
    base_radius = 180 + min(node_count * 10, 120)

    for idx, entity in enumerate(entities):
        node_id = entity.node_id()
        entity_map[node_id] = entity
        degree_count = degree.get(node_id, 0)
        color = _node_color(entity.type)
        icon = _node_icon(entity.type)
        short_label = entity.value if len(entity.value) <= 24 else entity.value[:22] + "…"
        angle = (2 * math.pi * idx) / node_count
        radius = base_radius + (60 if degree_count == 0 else 0)
        x = round(math.cos(angle) * radius, 2)
        y = round(math.sin(angle) * radius, 2)
        size = min(18 + degree_count * 5 + int(entity.confidence * 6), 54)
        tooltip = (
            f"<b>{entity.type}</b><br>"
            f"{entity.value}<br>"
            f"confidence: {int(entity.confidence * 100)}%<br>"
            f"degree: {degree_count}"
        )

        net.add_node(
            node_id,
            label=f"{icon} {short_label}",
            title=tooltip,
            color={
                "background": color,
                "border": color,
                "highlight": {"background": color, "border": "#ffffff"},
                "hover": {"background": color, "border": "#ffffff"},
            },
            size=size,
            x=x,
            y=y,
        )

    for link in links:
        if link.a not in entity_map or link.b not in entity_map:
            continue
        style = _edge_style(link.confidence)
        pct = int(link.confidence * 100)
        tooltip = (
            f"<b>{link.link_type}</b><br>"
            f"{link.explanation or 'Связь между сущностями'}<br>"
            f"confidence: {pct}%"
        )

        net.add_edge(
            link.a,
            link.b,
            title=tooltip,
            label=f"{pct}%" if link.confidence >= config.MEDIUM_LINK_THRESHOLD else "",
            color=style["color"],
            width=style["width"],
            dashes=style["dashes"],
        )

    return _save_network_html(
        net,
        "inv",
        investigation_id,
        f"OverNetting • INV #{investigation_id:04d}",
    )


def build_posts_match_graph_html(
    query: str,
    matches: list[dict],
    investigation_id: int,
) -> str:
    """
    Star graph: central query node -> matched channel posts (+ top entities per post).
    """
    net = _create_network()
    query_label = _truncate(query.strip() or "Запрос", 28)
    query_id = "query::root"
    query_color = "#39ff14"

    net.add_node(
        query_id,
        label=f"🔍 {query_label}",
        title=f"<b>Запрос</b><br>{html_module.escape(query)}",
        color={
            "background": query_color,
            "border": query_color,
            "highlight": {"background": query_color, "border": "#ffffff"},
            "hover": {"background": query_color, "border": "#ffffff"},
        },
        size=36,
        x=0,
        y=0,
    )

    normalized = _normalize_scores(matches)
    post_count = max(len(matches), 1)
    base_radius = 220

    for idx, (match, confidence) in enumerate(zip(matches, normalized)):
        message_id = int(match.get("message_id") or 0)
        post_id = f"post::{message_id}"
        date_str = (match.get("date") or "")[:10]
        raw_text = (match.get("text") or "").strip()
        snippet = _truncate(raw_text.replace("\n", " "), 200)
        score_raw = float(match.get("score") or 0)

        angle = (2 * math.pi * idx) / post_count
        x = round(math.cos(angle) * base_radius, 2)
        y = round(math.sin(angle) * base_radius, 2)

        link_line = ""
        if config.CHANNEL_PUBLIC_USERNAME and message_id:
            link_line = f"<br>t.me/{config.CHANNEL_PUBLIC_USERNAME}/{message_id}"

        tooltip = (
            f"<b>Пост #{message_id}</b><br>"
            f"Дата: {date_str}<br>"
            f"Релевантность: {score_raw:.2f}{link_line}<br><br>"
            f"{html_module.escape(snippet)}"
        )

        net.add_node(
            post_id,
            label=f"📄 #{message_id}",
            title=tooltip,
            color={
                "background": "#1a3a30",
                "border": "#20c997",
                "highlight": {"background": "#20c997", "border": "#ffffff"},
                "hover": {"background": "#20c997", "border": "#ffffff"},
            },
            size=22 + int(confidence * 12),
            x=x,
            y=y,
        )

        style = _edge_style(confidence)
        net.add_edge(
            query_id,
            post_id,
            title=f"Совпадение: {int(confidence * 100)}%",
            label=f"{int(confidence * 100)}%" if confidence >= config.MEDIUM_LINK_THRESHOLD else "",
            color=style["color"],
            width=style["width"],
            dashes=style["dashes"],
        )

        # Up to 2 entities per post for richer context
        for entity in extract_entities(raw_text)[:2]:
            entity_id = f"{post_id}::{entity.node_id()}"
            color = _node_color(entity.type)
            icon = _node_icon(entity.type)
            short = _truncate(entity.value, 20)
            net.add_node(
                entity_id,
                label=f"{icon} {short}",
                title=f"<b>{entity.type}</b><br>{entity.value}",
                color={
                    "background": color,
                    "border": color,
                    "highlight": {"background": color, "border": "#ffffff"},
                    "hover": {"background": color, "border": "#ffffff"},
                },
                size=14,
            )
            net.add_edge(
                post_id,
                entity_id,
                color="#2f6b4a",
                width=1,
                dashes=True,
            )

    return _save_network_html(
        net,
        "posts",
        investigation_id,
        f"OverNetting • POSTS MATCH #{investigation_id:04d}",
    )


async def html_to_png(html_path: str) -> Optional[str]:
    """
    Renders the HTML graph to PNG via Playwright.
    """
    png_path = html_path.replace(".html", ".png")
    try:
        from playwright.async_api import async_playwright

        async with async_playwright() as p:
            attempts = [
                {"browser": "chromium", "kwargs": {}},
                {"browser": "chromium", "kwargs": {"channel": "msedge"}},
                {"browser": "chromium", "kwargs": {"channel": "chrome"}},
            ]
            last_exc = None

            for attempt in attempts:
                browser = None
                try:
                    browser_api = getattr(p, attempt["browser"])
                    browser = await browser_api.launch(**attempt["kwargs"])
                    page = await browser.new_page(viewport={"width": 1600, "height": 980})
                    await page.goto(f"file://{os.path.abspath(html_path)}", wait_until="load")
                    await page.wait_for_timeout(1800)
                    await page.evaluate("() => window.__overnettingFit && window.__overnettingFit()")
                    await page.wait_for_timeout(1200)
                    await page.screenshot(path=png_path, full_page=False)
                    await browser.close()
                    return png_path
                except Exception as exc:
                    last_exc = exc
                    if browser:
                        await browser.close()

            raise last_exc or RuntimeError("Unable to render graph PNG.")
    except Exception as exc:
        print(f"[graph] PNG render unavailable ({exc}), returning HTML")
        return None


def build_summary(entities: list[Entity], links: list[Link]) -> str:
    strong = [link for link in links if link.confidence >= config.STRONG_LINK_THRESHOLD]
    medium = [
        link for link in links
        if config.MEDIUM_LINK_THRESHOLD <= link.confidence < config.STRONG_LINK_THRESHOLD
    ]
    weak = [link for link in links if link.confidence < config.MEDIUM_LINK_THRESHOLD]

    degree: dict[str, int] = {}
    for link in links:
        degree[link.a] = degree.get(link.a, 0) + 1
        degree[link.b] = degree.get(link.b, 0) + 1

    hub_nid = max(degree, key=degree.get, default=None) if degree else None
    hub_entity = next((entity for entity in entities if entity.node_id() == hub_nid), None)

    type_list = ", ".join(sorted({entity.type for entity in entities})) if entities else "—"
    lines = [
        f"Сущностей: {len(entities)} ({type_list})",
        (
            f"Связей: {len(links)}  "
            f"[сильных: {len(strong)} · средних: {len(medium)} · слабых: {len(weak)}]"
        ),
    ]

    if hub_entity:
        lines.append(f"Главный узел: {hub_entity.display()} ({degree.get(hub_nid, 0)} связей)")

    top_links = sorted(links, key=lambda item: item.confidence, reverse=True)[:5]
    if top_links:
        lines.append("")
        entity_lookup = {entity.node_id(): entity for entity in entities}
        for link in top_links:
            left = entity_lookup.get(link.a)
            right = entity_lookup.get(link.b)
            if left and right:
                lines.append(
                    f"• {left.display()} → {right.display()} [{int(link.confidence * 100)}%]"
                )

    return "\n".join(lines)

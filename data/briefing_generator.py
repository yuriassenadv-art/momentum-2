# momentum-2/data/briefing_generator.py
"""Briefing compiler for Gemini consumption.

Reads news_data.json and social_sentiment.json, produces a compact
factual briefing (~1000-2000 chars). No analysis — just facts and numbers.
"""
import sys
import os
import json
import logging
from datetime import datetime, timezone

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))
from config import Config

logger = logging.getLogger(__name__)


def load_json(path: str) -> dict:
    """Load JSON file, return empty dict on failure."""
    try:
        with open(path) as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError) as e:
        logger.warning(f"Failed to load {path}: {e}")
        return {}


def format_number(n: float) -> str:
    """Format large numbers compactly: 1.2T, 450B, 3.5M."""
    if n >= 1e12:
        return f"${n / 1e12:.1f}T"
    if n >= 1e9:
        return f"${n / 1e9:.0f}B"
    if n >= 1e6:
        return f"${n / 1e6:.0f}M"
    return f"${n:,.0f}"


def build_briefing(news: dict, sentiment: dict) -> str:
    """Build compact factual briefing string.

    Target: 1000-2000 chars. Format: factual, no analysis.
    """
    lines = []
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    lines.append(f"CRYPTO BRIEFING — {ts}")
    lines.append("")

    # --- Global Market ---
    gm = news.get("global_market", {})
    if gm:
        mcap = format_number(gm.get("total_market_cap_usd", 0))
        vol = format_number(gm.get("total_volume_24h_usd", 0))
        btc_dom = gm.get("btc_dominance", 0)
        change = gm.get("market_cap_change_24h_pct", 0)
        sign = "+" if change >= 0 else ""
        lines.append(f"MARKET: MCap {mcap} ({sign}{change}% 24h) | Vol {vol} | BTC dom {btc_dom}%")

    # --- Fear & Greed ---
    fg = news.get("fear_greed", {})
    if fg:
        lines.append(f"SENTIMENT: Fear&Greed {fg.get('value', '?')}/100 ({fg.get('label', '?')})")

    lines.append("")

    # --- Trending ---
    trending = news.get("trending_coins", [])
    if trending:
        top_trending = [f"{c['symbol']}" for c in trending[:7]]
        lines.append(f"TRENDING: {', '.join(top_trending)}")

    # --- Sentiment votes ---
    assets = sentiment.get("assets", {})
    if assets:
        bullish = []
        bearish = []
        for symbol, info in assets.items():
            up = info.get("up_pct", 50)
            if up >= 65:
                bullish.append(f"{symbol}({up}%)")
            elif up <= 35:
                bearish.append(f"{symbol}({up}%)")

        if bullish:
            lines.append(f"BULLISH: {', '.join(bullish)}")
        if bearish:
            lines.append(f"BEARISH: {', '.join(bearish)}")

    # --- Trending flags from sentiment ---
    trending_assets = [s for s, info in assets.items() if info.get("trending")]
    if trending_assets:
        lines.append(f"TRACKED+TRENDING: {', '.join(trending_assets)}")

    lines.append("")

    # --- Headlines (top 8, one-line each) ---
    headlines = news.get("headlines", [])
    if headlines:
        lines.append("HEADLINES:")
        for h in headlines[:8]:
            title = h.get("title", "")
            source = h.get("source", "")
            # Truncate long titles
            if len(title) > 100:
                title = title[:97] + "..."
            line = f"- {title}"
            if source:
                line += f" [{source}]"
            lines.append(line)

    briefing = "\n".join(lines)

    # Hard cap at 2000 chars
    if len(briefing) > 2000:
        briefing = briefing[:1997] + "..."

    return briefing


def generate_briefing(cfg: Config = None) -> dict:
    """Main briefing generation routine.

    Reads news_data.json and social_sentiment.json, produces briefing.

    Returns:
        Dict with briefing text and metadata.
    """
    if cfg is None:
        cfg = Config()

    news_path = os.path.join(cfg.base_dir, "news_data.json")
    sentiment_path = os.path.join(cfg.base_dir, "social_sentiment.json")

    news = load_json(news_path)
    sentiment = load_json(sentiment_path)

    if not news and not sentiment:
        logger.warning("No news or sentiment data found. Run collectors first.")
        briefing_text = "CRYPTO BRIEFING — No data available. Run collectors first."
    else:
        briefing_text = build_briefing(news, sentiment)

    result = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "briefing": briefing_text,
        "sources": {
            "news": bool(news),
            "sentiment": bool(sentiment),
        },
        "char_count": len(briefing_text),
    }

    # Save JSON
    briefing_json_path = os.path.join(cfg.base_dir, "briefing.json")
    with open(briefing_json_path, "w") as f:
        json.dump(result, f, indent=2)

    # Save markdown
    briefing_md_path = os.path.join(cfg.base_dir, "briefing.md")
    with open(briefing_md_path, "w") as f:
        f.write(f"```\n{briefing_text}\n```\n")

    logger.info(f"Briefing generated: {result['char_count']} chars")
    return result


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")
    result = generate_briefing()
    print(result["briefing"])
    print(f"\n--- {result['char_count']} chars ---")

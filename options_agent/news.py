"""
Free news headline fetch + lightweight sentiment scoring.

Uses yfinance's Ticker.news (no API key, but no push/streaming either -- it
reflects whatever Yahoo's endpoint has indexed, often lagging the actual
publish time by minutes) and VADER, a small lexicon-based sentiment tool that
runs locally with no external call. VADER is a general-purpose sentiment
model, not a finance-tuned one -- it can misread domain phrases (e.g. "beat
guidance" reads as neutral, "warns of headwinds" reads more negative than a
trader might weight it). Treat this as one weak, explainable signal to
combine with technicals, not a ground-truth read of market-moving news.
"""
from datetime import datetime, timedelta, timezone

import yfinance as yf
from vaderSentiment.vaderSentiment import SentimentIntensityAnalyzer

_analyzer = SentimentIntensityAnalyzer()


def _parse_timestamp(ts):
    if ts is None:
        return None
    try:
        if isinstance(ts, (int, float)):
            return datetime.fromtimestamp(ts, tz=timezone.utc)
        return datetime.fromisoformat(str(ts).replace("Z", "+00:00"))
    except (ValueError, TypeError):
        return None


def fetch_recent_headlines(ticker, lookback_hours=24):
    """Returns a list of headline strings published within lookback_hours.
    yfinance's news payload schema has shifted across versions (flat dict vs
    nested under 'content'), so both shapes are handled defensively."""
    try:
        news_items = yf.Ticker(ticker).news or []
    except Exception:
        return []

    cutoff = datetime.now(timezone.utc) - timedelta(hours=lookback_hours)
    headlines = []
    for item in news_items:
        content = item.get("content", item) if isinstance(item, dict) else {}
        title = content.get("title") or item.get("title")
        ts = content.get("pubDate") or item.get("providerPublishTime")
        published = _parse_timestamp(ts)
        if title and (published is None or published >= cutoff):
            headlines.append(title)
    return headlines


def get_news_sentiment(ticker, lookback_hours=24):
    """Returns (compound_sentiment, headlines).
    compound_sentiment is the mean VADER compound score across recent
    headlines, in [-1, 1], or None if no headlines were found (distinct from
    a genuinely neutral 0.0)."""
    headlines = fetch_recent_headlines(ticker, lookback_hours)
    if not headlines:
        return None, []
    scores = [_analyzer.polarity_scores(h)["compound"] for h in headlines]
    return sum(scores) / len(scores), headlines

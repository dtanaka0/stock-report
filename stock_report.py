import anthropic
import smtplib
import os
import json
import time
import requests
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from datetime import datetime
import feedparser
import markdown
import yfinance as yf

STOCKS = {
    "NVIDIA":           "NVDA",
    "Alphabet":         "GOOGL",
    "SK Hynix":         "000660.KS",
    "Linde":            "LIN",
    "BWX Technologies": "BWXT",
}

INDUSTRIES = {
    "半導体業界":     "semiconductor+industry+2026",
    "宇宙・防衛関連": "space+defense+industry+2026",
}

GMAIL_ADDRESS      = os.environ.get("GMAIL_ADDRESS")
GMAIL_PASSWORD     = os.environ.get("GMAIL_PASSWORD")
TO_ADDRESS         = os.environ.get("TO_ADDRESS")
ANTHROPIC_API_KEY  = os.environ.get("ANTHROPIC_API_KEY")
ALPHA_VANTAGE_KEY  = os.environ.get("ALPHA_VANTAGE_KEY")

# ============================================================
# 1-A. 株価データ取得（yfinance：メイン）
# ============================================================

def get_stock_data_yfinance(ticker, name):
    try:
        session = requests.Session()
        session.headers.update({
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/120.0.0.0 Safari/537.36"
            )
        })

        stock = yf.Ticker(ticker, session=session)
        hist  = stock.history(period="5d", auto_adjust=True)

        if hist.empty:
            print(f"  ⚠️ {name}: yfinanceデータなし → Alpha Vantageで再取得")
            return None

        latest = hist.iloc[-1]
        prev   = hist.iloc[-2] if len(hist) >= 2 else hist.iloc[-1]
        change     = float(latest["Close"]) - float(prev["Close"])
        change_pct = (change / float(prev["Close"])) * 100

        # ★ fast_infoを使用（軽量で取得しやすい）
        fi = stock.fast_info
        market_cap_raw = getattr(fi, "market_cap", None)
        week52_high    = getattr(fi, "fifty_two_week_high", "N/A")
        week52_low     = getattr(fi, "fifty_two_week_low", "N/A")

        if market_cap_raw:
            if market_cap_raw >= 1_000_000_000_000:
                market_cap_str = f"${market_cap_raw/1_000_000_000_000:.1f}兆"
            else:
                market_cap_str = f"${market_cap_raw/1_000_000_000:.0f}十億"
        else:
            market_cap_str = "N/A"

        # PERはinfoから取るが失敗しても続行
        pe_ratio = "N/A"
        try:
            info     = stock.get_info()
            pe_ratio = info.get("trailingPE", "N/A")
        except Exception:
            pass

        history_5d = []
        for idx, row in hist.iterrows():
            history_5d.append({
                "日付": str(idx.date()),
                "終値": round(float(row["Close"]), 2),
                "出来高": int(row["Volume"]),
            })

        return {
            "name":       name,
            "ticker":     ticker,
            "source":     "yfinance",
            "price":      round(float(latest["Close"]), 2),
            "change":     round(change, 2),
            "change_pct": round(change_pct, 2),
            "volume":     int(latest["Volume"]),
            "5d_high":    round(float(hist["High"].max()), 2),
            "5d_low":     round(float(hist["Low"].min()), 2),
            "ma5":        round(float(hist["Close"].mean()), 2),
            "pe_ratio":   pe_ratio,
            "market_cap": market_cap_str,
            "52w_high":   week52_high,
            "52w_low":    week52_low,
            "history_5d": history_5d,
        }

    except Exception as e:
        print(f"  ⚠️ {name}: yfinanceエラー ({e}) → Alpha Vantageで再取得")
        return None

# ============================================================
# 1-B. 株価データ取得（Alpha Vantage：フォールバック）
# ============================================================

def get_stock_data_alphavantage(ticker, name):
    """Alpha Vantageでの株価取得（yfinance失敗時のバックアップ）"""
    try:
        url = (
            f"https://www.alphavantage.co/query"
            f"?function=GLOBAL_QUOTE"
            f"&symbol={ticker}"
            f"&apikey={ALPHA_VANTAGE_KEY}"
        )
        response = requests.get(url, timeout=10)
        data     = response.json()
        quote    = data.get("Global Quote", {})

        if not quote or not quote.get("05. price"):
            return {"name": name, "ticker": ticker, "error": "データ取得失敗"}

        return {
            "name":       name,
            "ticker":     ticker,
            "source":     "Alpha Vantage",
            "price":      round(float(quote.get("05. price", 0)), 2),
            "change":     round(float(quote.get("09. change", 0)), 2),
            "change_pct": round(float(quote.get("10. change percent", "0%").replace("%", "")), 2),
            "volume":     int(quote.get("06. volume", 0)),
            "high":       round(float(quote.get("03. high", 0)), 2),
            "low":        round(float(quote.get("04. low", 0)), 2),
            "latest_day": quote.get("07. latest trading day", "不明"),
        }
    except Exception as e:
        return {"name": name, "ticker": ticker, "error": str(e)}

# ============================================================
# 1. 株価データ取得（yfinance優先、失敗時はAlpha Vantage）
# ============================================================

def get_stock_data(ticker, name):
    data = get_stock_data_yfinance(ticker, name)
    if data is not None:
        return data
    time.sleep(13)  # Alpha Vantage API制限対策
    return get_stock_data_alphavantage(ticker, name)

# ============================================================
# 2. ニュース取得
# ============================================================

def get_news(query, max_items=5):
    try:
        safe_query = query.replace(" ", "+")
        url  = f"https://news.google.com/rss/search?q={safe_query}&hl=ja&gl=JP&ceid=JP:ja"
        feed = feedparser.parse(url)
        news_list = []
        for entry in feed.entries[:max_items]:
            pub = entry.get("published", "")
            news_list.append(f"- {entry.title}（{pub[:16]}）")
        return news_list if news_list else ["関連ニュースなし"]
    except Exception as e:
        return [f"ニュース取得失敗: {str(e)}"]

# ============================================================
# 3. Claude APIでレポート生成
# ============================================================

def generate_report(stocks_data, news_data, industry_news):
    client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)
    today  = datetime.now().strftime("%Y年%m月%d日")

    prompt = f"""
あなたは経験豊富な投資アナリストです。
以下の株価データ・ニュース・業界動向を分析し、個人投資家向けの日次レポートを作成してください。

## 今日の日付
{today}

## 株価データ（yfinance または Alpha Vantage）
{json.dumps(stocks_data, ensure_ascii=False, indent=2)}

## 銘柄別ニュース
{json.dumps(news_data, ensure_ascii=False, indent=2)}

## 業界動向ニュース
{json.dumps(industry_news, ensure_ascii=False, indent=2)}

## レポート出力形式の指示
Markdownで出力してください。以下の構成で作成してください：

---

# 📊 個人投資家向け日次レポート
**{today}**

---

## ① 銘柄別分析

各銘柄を以下の形式で：

### 🟢/🔴 銘柄名（ティッカー）｜ $価格 ▲▼騰落率%

#### 株価サマリー
| 項目 | 数値 |
|------|------|
| 終値 | $xxx |
| 前日比 | +$x.xx (+x.xx%) |
| 出来高 | xxx万株 |
| 高値/安値（当日） | $xxx / $xxx |
| PER | xx倍（yfinanceデータがある場合） |
| 時価総額 | $xxx兆（yfinanceデータがある場合） |
| 52週高値/安値 | $xxx / $xxx（yfinanceデータがある場合） |

（株価の特徴を2-3行で）

#### 主要ニュース
- ニュース要点

#### 短期見通し（1〜2週間）
（見通しを2-3行で）

#### 売買判定
**✅ 買い増し推奨 / ⚠️ ホールド / 🔴 売り検討**
（理由を2-3行で）

---

## ② 業界トレンド

### 半導体業界
（動向と注目点）

### 宇宙・防衛関連
（動向と注目点）

---

## ③ 本日の総評
（全体コメント150文字程度）

---
⚠️ 本レポートは参考情報です。投資判断はご自身の責任で行ってください。
"""

    response = client.messages.create(
        model="claude-sonnet-4-6",
        max_tokens=3000,
        messages=[{"role": "user", "content": prompt}]
    )
    return response.content[0].text

# ============================================================
# 4. HTMLメール送信
# ============================================================

def markdown_to_html(md_text):
    html_body = markdown.markdown(md_text, extensions=["tables", "nl2br"])
    return f"""
<!DOCTYPE html>
<html>
<head>
<meta charset="utf-8">
<style>
  body {{
    font-family: -apple-system, BlinkMacSystemFont, 'Helvetica Neue', sans-serif;
    font-size: 15px;
    line-height: 1.7;
    color: #1a1a1a;
    background: #f5f5f5;
    margin: 0;
    padding: 20px;
  }}
  .container {{
    max-width: 680px;
    margin: 0 auto;
    background: #ffffff;
    border-radius: 12px;
    padding: 32px;
    box-shadow: 0 2px 8px rgba(0,0,0,0.08);
  }}
  h1 {{ font-size: 22px; color: #1a1a1a; border-bottom: 3px solid #0066cc; padding-bottom: 12px; }}
  h2 {{ font-size: 18px; color: #0066cc; margin-top: 32px; border-left: 4px solid #0066cc; padding-left: 10px; }}
  h3 {{ font-size: 16px; color: #1a1a1a; background: #f0f4ff; padding: 10px 14px; border-radius: 8px; margin-top: 24px; }}
  h4 {{ font-size: 14px; color: #444; margin-top: 16px; margin-bottom: 6px; }}
  table {{ width: 100%; border-collapse: collapse; margin: 12px 0; font-size: 14px; }}
  th, td {{ padding: 8px 12px; text-align: left; border-bottom: 1px solid #e8e8e8; }}
  th {{ background: #f0f4ff; color: #0066cc; font-weight: 600; }}
  ul, ol {{ padding-left: 20px; margin: 8px 0; }}
  li {{ margin-bottom: 4px; }}
  hr {{ border: none; border-top: 1px solid #e8e8e8; margin: 24px 0; }}
  blockquote {{ background: #fff8e1; border-left: 4px solid #ffc107; margin: 12px 0; padding: 10px 16px; border-radius: 0 8px 8px 0; font-size: 13px; color: #666; }}
  p {{ margin: 8px 0; }}
</style>
</head>
<body>
  <div class="container">{html_body}</div>
</body>
</html>
"""

def send_email(report):
    today        = datetime.now().strftime("%Y/%m/%d")
    to_addresses = [addr.strip() for addr in TO_ADDRESS.split(",")]

    msg            = MIMEMultipart("alternative")
    msg["Subject"] = f"📊 株式日次レポート {today}"
    msg["From"]    = GMAIL_ADDRESS
    msg["To"]      = ", ".join(to_addresses)

    msg.attach(MIMEText(report, "plain", "utf-8"))
    msg.attach(MIMEText(markdown_to_html(report), "html", "utf-8"))

    with smtplib.SMTP_SSL("smtp.gmail.com", 465) as server:
        server.login(GMAIL_ADDRESS, GMAIL_PASSWORD)
        server.sendmail(GMAIL_ADDRESS, to_addresses, msg.as_string())

    print(f"✅ メール送信完了（{len(to_addresses)}件）")

# ============================================================
# メイン処理
# ============================================================

def main():
    print("📈 株式レポート生成開始...")

    stocks_data = []
    for name, ticker in STOCKS.items():
        data = get_stock_data(ticker, name)
        stocks_data.append(data)
        src   = data.get("source", "エラー")
        price = data.get("price", "エラー")
        pct   = data.get("change_pct", "-")
        print(f"  {name}: {price} ({pct}%) [{src}]")
        time.sleep(2)

    news_data = {}
    for name in STOCKS.keys():
        news = get_news(name)
        news_data[name] = news
        print(f"  {name}: ニュース{len(news)}件取得")

    industry_news = {}
    for industry, query in INDUSTRIES.items():
        news = get_news(query)
        industry_news[industry] = news
        print(f"  {industry}: ニュース{len(news)}件取得")

    print("🤖 Claudeでレポート生成中...")
    report = generate_report(stocks_data, news_data, industry_news)

    print("\n" + "="*60)
    print(report)
    print("="*60)

    if GMAIL_ADDRESS and GMAIL_PASSWORD and TO_ADDRESS:
        send_email(report)
    else:
        print("⚠️ メール設定未完了のため送信スキップ")

if __name__ == "__main__":
    import traceback
    try:
        main()
    except Exception as e:
        print("❌ エラー発生:")
        print(traceback.format_exc())
        raise

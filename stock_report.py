import anthropic
import smtplib
import os
import json
import time
import requests
import base64
import io
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from datetime import datetime
import feedparser
import markdown
import yfinance as yf
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import matplotlib.dates as mdates
from matplotlib import rcParams
rcParams["font.family"] = "DejaVu Sans"

STOCKS = {
    "NVIDIA":           "NVDA",
    "Alphabet":         "GOOGL",
    "SK Hynix":         "000660.KS",
    "Linde":            "LIN",
    "BWX Technologies": "BWXT",
}

INDUSTRIES = {
    "半導体業界": "semiconductor+industry+2026",
    "原子力業界": "nuclear+energy+industry+2026",
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
        stock = yf.Ticker(ticker)
        hist  = stock.history(period="5d", auto_adjust=True)

        if hist.empty:
            print(f"  ⚠️ {name}: yfinanceデータなし → Alpha Vantageで再取得")
            return None

        latest = hist.iloc[-1]
        prev   = hist.iloc[-2] if len(hist) >= 2 else hist.iloc[-1]
        change     = float(latest["Close"]) - float(prev["Close"])
        change_pct = (change / float(prev["Close"])) * 100

        fi = stock.fast_info
        market_cap_raw = getattr(fi, "market_cap", None)
        week52_high    = getattr(fi, "fifty_two_week_high", "N/A")
        week52_low     = getattr(fi, "fifty_two_week_low", "N/A")

        if market_cap_raw:
            if market_cap_raw >= 1_000_000_000_000:
                market_cap_str = f"${market_cap_raw/1_000_000_000_000:.1f}T"
            else:
                market_cap_str = f"${market_cap_raw/1_000_000_000:.0f}B"
        else:
            market_cap_str = "N/A"

        pe_ratio = "N/A"
        try:
            info     = stock.info
            pe_ratio = info.get("trailingPE", "N/A")
        except Exception:
            pass

        # グラフ用に履歴データを保持
        history_5d = []
        for idx, row in hist.iterrows():
            history_5d.append({
                "date":   idx,
                "close":  round(float(row["Close"]), 2),
                "volume": int(row["Volume"]),
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
            "history_5d": [],
        }
    except Exception as e:
        return {"name": name, "ticker": ticker, "error": str(e), "history_5d": []}

def get_stock_data(ticker, name):
    data = get_stock_data_yfinance(ticker, name)
    if data is not None:
        return data
    time.sleep(13)
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
# 3. グラフ生成（base64で返す）
# ============================================================

COLORS = {
    "NVIDIA":           "#76b900",
    "Alphabet":         "#4285f4",
    "SK Hynix":         "#e63946",
    "Linde":            "#f4a261",
    "BWX Technologies": "#7b2d8b",
}

def make_price_chart(stock_data):
    """各銘柄の5日間株価チャートを生成"""
    history = stock_data.get("history_5d", [])
    if len(history) < 2:
        return None

    dates  = [h["date"] for h in history]
    prices = [h["close"] for h in history]
    name   = stock_data["name"]
    color  = COLORS.get(name, "#0066cc")
    pct    = stock_data.get("change_pct", 0)
    trend_color = "#2ecc71" if pct >= 0 else "#e74c3c"

    fig, ax = plt.subplots(figsize=(6, 2.5))
    fig.patch.set_facecolor("#ffffff")
    ax.set_facecolor("#f8faff")

    ax.plot(dates, prices, color=color, linewidth=2.5, zorder=3)
    ax.fill_between(dates, prices, min(prices) * 0.999,
                    alpha=0.15, color=color)

    ax.xaxis.set_major_formatter(mdates.DateFormatter("%m/%d"))
    ax.tick_params(labelsize=8, colors="#666")
    for spine in ax.spines.values():
        spine.set_color("#e0e0e0")
    ax.grid(axis="y", color="#e8e8e8", linestyle="--", linewidth=0.8)

    arrow = "▲" if pct >= 0 else "▼"
    ax.set_title(
        f"{name}  ${prices[-1]:.2f}  {arrow}{abs(pct):.2f}%",
        fontsize=10, fontweight="bold", color="#1a1a1a", pad=8
    )

    plt.tight_layout()
    buf = io.BytesIO()
    plt.savefig(buf, format="png", dpi=130, bbox_inches="tight",
                facecolor="#ffffff")
    plt.close(fig)
    buf.seek(0)
    return base64.b64encode(buf.read()).decode("utf-8")


def make_comparison_chart(stocks_data):
    """全銘柄の騰落率比較バーチャートを生成"""
    names = []
    pcts  = []
    colors = []

    for s in stocks_data:
        if "error" not in s:
            names.append(s["name"].replace(" Technologies", ""))
            pct = s.get("change_pct", 0)
            pcts.append(pct)
            colors.append("#2ecc71" if pct >= 0 else "#e74c3c")

    if not names:
        return None

    fig, ax = plt.subplots(figsize=(6, 2.8))
    fig.patch.set_facecolor("#ffffff")
    ax.set_facecolor("#f8faff")

    bars = ax.bar(names, pcts, color=colors, width=0.5, zorder=3)
    ax.axhline(0, color="#999", linewidth=0.8)

    for bar, pct in zip(bars, pcts):
        ax.text(
            bar.get_x() + bar.get_width() / 2,
            bar.get_height() + (0.05 if pct >= 0 else -0.15),
            f"{pct:+.2f}%",
            ha="center", va="bottom", fontsize=8, color="#333"
        )

    ax.tick_params(labelsize=8, colors="#666")
    ax.set_ylabel("騰落率 (%)", fontsize=8, color="#666")
    for spine in ax.spines.values():
        spine.set_color("#e0e0e0")
    ax.grid(axis="y", color="#e8e8e8", linestyle="--", linewidth=0.8)
    ax.set_title("本日の騰落率比較", fontsize=10,
                 fontweight="bold", color="#1a1a1a", pad=8)

    plt.tight_layout()
    buf = io.BytesIO()
    plt.savefig(buf, format="png", dpi=130, bbox_inches="tight",
                facecolor="#ffffff")
    plt.close(fig)
    buf.seek(0)
    return base64.b64encode(buf.read()).decode("utf-8")

# ============================================================
# 4. Claude APIでレポート生成
# ============================================================

def generate_report(stocks_data, news_data, industry_news):
    client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)
    today  = datetime.now().strftime("%Y年%m月%d日")

    stocks_for_prompt = [
        {k: v for k, v in s.items() if k != "history_5d"}
        for s in stocks_data
    ]

    prompt = f"""
あなたは経験豊富な投資アナリストです。
以下の株価データ・ニュース・業界動向を分析し、個人投資家向けの日次レポートを作成してください。

## 今日の日付
{today}

## 株価データ（直近取引日）
{json.dumps(stocks_for_prompt, ensure_ascii=False, indent=2)}

## 銘柄別ニュース
{json.dumps(news_data, ensure_ascii=False, indent=2)}

## 業界動向ニュース
{json.dumps(industry_news, ensure_ascii=False, indent=2)}

## レポート出力形式の指示
Markdownで以下の順番で必ず全セクションを出力してください：

# 📊 個人投資家向け日次レポート
**{today}**

---

## ① 業界トレンド

### 📡 半導体業界
- 主要な動きと市場トレンド（3〜4行）
- 今後1〜2週間の注目ポイント
- 保有銘柄（NVIDIA・SK Hynix）への影響

### ⚛️ 原子力業界
- 主要な動きと市場トレンド（3〜4行）
- 今後1〜2週間の注目ポイント
- 保有銘柄（BWX Technologies・Linde）への影響

---

## ② 注目銘柄ピックアップ

保有していない銘柄から今注目すべき3銘柄を提案：

| 銘柄名（ティッカー） | 注目理由 | リスク | 短期期待値 |
|---|---|---|---|
| 例 | 理由 | リスク | 高/中/低 |

---

## ③ 銘柄別分析

各銘柄を以下の形式で：

### 🟢/🔴 銘柄名（ティッカー）｜ $価格 ▲▼騰落率%

CHART_PLACEHOLDER

#### 株価サマリー
| 項目 | 数値 |
|------|------|
| 終値 | $xxx |
| 前日比 | +$x.xx (+x.xx%) |
| 出来高 | xxx万株 |
| 高値/安値 | $xxx / $xxx |
| PER | xx倍 |
| 時価総額 | $xxx |

（株価の特徴を2行で）

#### 主要ニュース
- 要点を2〜3行で

#### 売買判定
**✅ 買い増し推奨 / ⚠️ ホールド / 🔴 売り検討**
（理由を2行で）

---

## ④ 本日の総評
（200文字程度）

---
⚠️ 本レポートは参考情報です。投資判断はご自身の責任で行ってください。
"""

    response = client.messages.create(
        model="claude-sonnet-4-6",
        max_tokens=6000,
        messages=[{"role": "user", "content": prompt}]
    )
    return response.content[0].text

# ============================================================
# 5. MarkdownをHTML化してグラフを埋め込む
# ============================================================

def build_html_email(md_text, stocks_data):
    """グラフを生成してHTMLメールに埋め込む"""

    # 比較チャートを生成
    comparison_b64 = make_comparison_chart(stocks_data)
    comparison_img = (
        f'<img src="data:image/png;base64,{comparison_b64}" '
        f'style="width:100%;max-width:580px;margin:12px 0;" />'
        if comparison_b64 else ""
    )

    # 各銘柄チャートを生成（名前をキーに）
    charts = {}
    for s in stocks_data:
        b64 = make_price_chart(s)
        if b64:
            charts[s["name"]] = (
                f'<img src="data:image/png;base64,{b64}" '
                f'style="width:100%;max-width:580px;margin:8px 0;" />'
            )

    # CHART_PLACEHOLDERを各銘柄チャートに置換
    current_stock = [None]

    def replace_placeholder(line):
        if line.strip().startswith("### "):
            for name in charts:
                if name in line:
                    current_stock[0] = name
                    break
        if "CHART_PLACEHOLDER" in line:
            img = charts.get(current_stock[0], "")
            return line.replace("CHART_PLACEHOLDER", img)
        return line

    lines = md_text.split("\n")
    lines = [replace_placeholder(l) for l in lines]
    md_text_replaced = "\n".join(lines)

    html_body = markdown.markdown(
        md_text_replaced, extensions=["tables", "nl2br"]
    )

    # 比較チャートをレポート先頭に挿入
    html_body = html_body.replace(
        "<hr />", comparison_img + "<hr />", 1
    )

    return f"""<!DOCTYPE html>
<html>
<head>
<meta charset="utf-8">
<style>
  body {{
    font-family: -apple-system, BlinkMacSystemFont, 'Helvetica Neue', sans-serif;
    font-size: 15px; line-height: 1.7; color: #1a1a1a;
    background: #f5f5f5; margin: 0; padding: 20px;
  }}
  .container {{
    max-width: 680px; margin: 0 auto; background: #fff;
    border-radius: 12px; padding: 32px;
    box-shadow: 0 2px 8px rgba(0,0,0,0.08);
  }}
  h1 {{ font-size: 22px; color: #1a1a1a;
        border-bottom: 3px solid #0066cc; padding-bottom: 12px; }}
  h2 {{ font-size: 18px; color: #0066cc; margin-top: 32px;
        border-left: 4px solid #0066cc; padding-left: 10px; }}
  h3 {{ font-size: 16px; color: #1a1a1a; background: #f0f4ff;
        padding: 10px 14px; border-radius: 8px; margin-top: 24px; }}
  h4 {{ font-size: 14px; color: #444; margin-top: 16px; margin-bottom: 6px; }}
  table {{ width: 100%; border-collapse: collapse; margin: 12px 0; font-size: 14px; }}
  th, td {{ padding: 8px 12px; text-align: left; border-bottom: 1px solid #e8e8e8; }}
  th {{ background: #f0f4ff; color: #0066cc; font-weight: 600; }}
  ul, ol {{ padding-left: 20px; margin: 8px 0; }}
  li {{ margin-bottom: 4px; }}
  hr {{ border: none; border-top: 1px solid #e8e8e8; margin: 24px 0; }}
  blockquote {{ background: #fff8e1; border-left: 4px solid #ffc107;
                margin: 12px 0; padding: 10px 16px;
                border-radius: 0 8px 8px 0; font-size: 13px; color: #666; }}
  p {{ margin: 8px 0; }}
  img {{ border-radius: 8px; }}
</style>
</head>
<body>
  <div class="container">{html_body}</div>
</body>
</html>"""

# ============================================================
# 6. メール送信
# ============================================================

def send_email(report, stocks_data):
    today        = datetime.now().strftime("%Y/%m/%d")
    to_addresses = [addr.strip() for addr in TO_ADDRESS.split(",")]

    msg            = MIMEMultipart("alternative")
    msg["Subject"] = f"📊 株式日次レポート {today}"
    msg["From"]    = GMAIL_ADDRESS
    msg["To"]      = ", ".join(to_addresses)

    msg.attach(MIMEText(report, "plain", "utf-8"))
    msg.attach(MIMEText(build_html_email(report, stocks_data), "html", "utf-8"))

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
        send_email(report, stocks_data)
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

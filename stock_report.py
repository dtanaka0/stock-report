import yfinance as yf
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

GMAIL_ADDRESS     = os.environ.get("GMAIL_ADDRESS")
GMAIL_PASSWORD    = os.environ.get("GMAIL_PASSWORD")
TO_ADDRESS        = os.environ.get("TO_ADDRESS")
ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY")

# ============================================================
# 1. 株価データ取得
# ============================================================

def get_stock_data(ticker, name):
    try:
        stock = yf.Ticker(ticker)
        hist  = stock.history(period="5d", auto_adjust=True)
        if hist.empty:
            return {"name": name, "ticker": ticker, "error": "データなし（市場休場の可能性）"}
        latest = hist.iloc[-1]
        prev   = hist.iloc[-2] if len(hist) >= 2 else hist.iloc[-1]
        change     = float(latest["Close"]) - float(prev["Close"])
        change_pct = (change / float(prev["Close"])) * 100
        return {
            "name":       name,
            "ticker":     ticker,
            "price":      round(float(latest["Close"]), 2),
            "change":     round(change, 2),
            "change_pct": round(change_pct, 2),
            "volume":     int(latest["Volume"]),
            "ma5":        round(float(hist["Close"].mean()), 2),
            "5d_high":    round(float(hist["High"].max()), 2),
            "5d_low":     round(float(hist["Low"].min()), 2),
        }
    except Exception as e:
        return {"name": name, "ticker": ticker, "error": str(e)}

# ============================================================
# 2. ニュース取得（スペースを+に置換してURLエラーを回避）
# ============================================================

def get_news(query, max_items=5):
    try:
        # スペースを+に変換（URLエンコード）
        safe_query = query.replace(" ", "+")
        url  = f"https://news.google.com/rss/search?q={safe_query}&hl=ja&gl=JP&ceid=JP:ja"
        feed = feedparser.parse(url)
        news_list = []
        for entry in feed.entries[:max_items]:
            pub = entry.get("published", "")
            news_list.append(f"・{entry.title}（{pub[:16]}）")
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

## 株価データ（直近5営業日）
{json.dumps(stocks_data, ensure_ascii=False, indent=2)}

## 銘柄別ニュース
{json.dumps(news_data, ensure_ascii=False, indent=2)}

## 業界動向ニュース
{json.dumps(industry_news, ensure_ascii=False, indent=2)}

## レポート作成の指示

### ① 各銘柄の分析（5銘柄それぞれ）
1. 当日の株価サマリー（価格・騰落率・出来高の特徴）
2. 主要ニュースの要点
3. 短期見通し（今後1〜2週間）
4. 売買判定：以下から1つ選び理由を3行以内で
   - ✅ 買い増し推奨
   - ⚠️ ホールド（様子見）
   - 🔴 売り・利確検討

### ② 業界トレンドサマリー
- 半導体業界：主要な動きと今後の注目点
- 宇宙・防衛関連：主要な動きと今後の注目点
- 保有銘柄との関連性コメント

### ③ 本日の総評
5銘柄と業界動向を踏まえた全体コメントを150文字程度で。

※ 株価データが「エラー」の銘柄はニュースと業界動向のみで分析してください。
※ あくまで参考情報です。最終判断は必ずご自身で行ってください。
"""

    response = client.messages.create(
        model="claude-sonnet-4-6",
        max_tokens=3000,
        messages=[{"role": "user", "content": prompt}]
    )
    return response.content[0].text

# ============================================================
# 4. メール送信
# ============================================================

def send_email(report):
    today        = datetime.now().strftime("%Y/%m/%d")
    to_addresses = [addr.strip() for addr in TO_ADDRESS.split(",")]

    msg            = MIMEMultipart("alternative")
    msg["Subject"] = f"📊 株式日次レポート {today}"
    msg["From"]    = GMAIL_ADDRESS
    msg["To"]      = ", ".join(to_addresses)
    msg.attach(MIMEText(report, "plain", "utf-8"))

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
        print(f"  {name}: {data.get('price', 'エラー')} ({data.get('change_pct', '-')}%)")
        time.sleep(1)

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

"""
株式日次レポート自動生成スクリプト
- 株価データ取得（yfinance）
- ニュース取得（Google News RSS）
- Claude APIでレポート＆売買判定生成
- メール送信（Gmail）
"""

import yfinance as yf
import anthropic
import smtplib
import os
import json
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from datetime import datetime, timedelta
import feedparser  # Google News RSS用

# ============================================================
# ★ 設定：自分の銘柄・メール情報をここに入力
# ============================================================

STOCKS = {
    "トヨタ":     "7203.T",
    "ソフトバンクG": "9984.T",
    "NVIDIA":    "NVDA",
    "Apple":     "AAPL",
}

# メール設定（GitHub Secretsで管理するので直接書かない）
GMAIL_ADDRESS  = os.environ.get("GMAIL_ADDRESS")   # 送信元Gmailアドレス
GMAIL_PASSWORD = os.environ.get("GMAIL_PASSWORD")  # Gmailアプリパスワード
TO_ADDRESS     = os.environ.get("TO_ADDRESS")      # 送信先アドレス

# Claude APIキー
ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY")

# ============================================================
# 1. 株価データ取得
# ============================================================

def get_stock_data(ticker: str, name: str) -> dict:
    """過去5日間の株価データを取得"""
    stock = yf.Ticker(ticker)
    hist = stock.history(period="5d")

    if hist.empty:
        return {"name": name, "ticker": ticker, "error": "データ取得失敗"}

    latest = hist.iloc[-1]
    prev   = hist.iloc[-2] if len(hist) >= 2 else hist.iloc[-1]

    change     = latest["Close"] - prev["Close"]
    change_pct = (change / prev["Close"]) * 100

    # 5日間の移動平均・最高値・最安値
    ma5  = hist["Close"].mean()
    high = hist["High"].max()
    low  = hist["Low"].min()

    return {
        "name":       name,
        "ticker":     ticker,
        "price":      round(latest["Close"], 2),
        "change":     round(change, 2),
        "change_pct": round(change_pct, 2),
        "volume":     int(latest["Volume"]),
        "ma5":        round(ma5, 2),
        "5d_high":    round(high, 2),
        "5d_low":     round(low, 2),
    }

# ============================================================
# 2. ニュース取得（Google News RSS）
# ============================================================

def get_news(company_name: str, max_items: int = 5) -> list[str]:
    """Google News RSSからニュースを取得"""
    url = f"https://news.google.com/rss/search?q={company_name}&hl=ja&gl=JP&ceid=JP:ja"
    feed = feedparser.parse(url)

    news_list = []
    for entry in feed.entries[:max_items]:
        pub = entry.get("published", "")
        news_list.append(f"・{entry.title}（{pub[:16]}）")

    return news_list if news_list else ["関連ニュースなし"]

# ============================================================
# 3. Claude APIでレポート生成
# ============================================================

def generate_report(stocks_data: list[dict], news_data: dict) -> str:
    """Claude APIを使ってレポートと売買判定を生成"""
    client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)

    # プロンプト用データを整形
    stock_info = json.dumps(stocks_data, ensure_ascii=False, indent=2)
    news_info  = json.dumps(news_data,  ensure_ascii=False, indent=2)

    today = datetime.now().strftime("%Y年%m月%d日")

    prompt = f"""
あなたは経験豊富な投資アナリストです。
以下の株価データとニュースを分析し、個人投資家向けの日次レポートを作成してください。

## 今日の日付
{today}

## 株価データ（直近5営業日）
{stock_info}

## 関連ニュース
{news_info}

## レポート作成の指示
各銘柄について以下の形式で分析してください：

1. **当日の株価サマリー**（価格・騰落率・出来高の特徴）
2. **主要ニュースの要点**（投資に影響しそうな情報を抜粋）
3. **短期見通し**（今後1〜2週間の方向感）
4. **売買判定**：以下から1つ選び、理由を3行以内で説明
   - ✅ 買い増し推奨
   - ⚠️ ホールド（様子見）
   - 🔴 売り・利確検討

最後に、4銘柄全体を俯瞰した「本日の総評」を100文字程度で記載してください。

※ あくまで参考情報です。最終判断は必ずご自身で行ってください。
"""

    response = client.messages.create(
        model="claude-sonnet-4-6",
        max_tokens=2000,
        messages=[{"role": "user", "content": prompt}]
    )

    return response.content[0].text

# ============================================================
# 4. メール送信
# ============================================================

def send_email(report: str):
    """Gmailでレポートを送信"""
    today = datetime.now().strftime("%Y/%m/%d")

    msg = MIMEMultipart("alternative")
    msg["Subject"] = f"📊 株式日次レポート {today}"
    msg["From"]    = GMAIL_ADDRESS
    msg["To"]      = TO_ADDRESS

    # テキスト版
    part = MIMEText(report, "plain", "utf-8")
    msg.attach(part)

    with smtplib.SMTP_SSL("smtp.gmail.com", 465) as server:
        server.login(GMAIL_ADDRESS, GMAIL_PASSWORD)
        server.sendmail(GMAIL_ADDRESS, TO_ADDRESS, msg.as_string())

    print("✅ メール送信完了")

# ============================================================
# メイン処理
# ============================================================

def main():
    print("📈 株式レポート生成開始...")

    # 1. 株価データ取得
    stocks_data = []
    for name, ticker in STOCKS.items():
        data = get_stock_data(ticker, name)
        stocks_data.append(data)
        print(f"  {name}: {data.get('price', 'エラー')} ({data.get('change_pct', '-')}%)")

    # 2. ニュース取得
    news_data = {}
    for name in STOCKS.keys():
        news = get_news(name)
        news_data[name] = news
        print(f"  {name}: ニュース{len(news)}件取得")

    # 3. レポート生成
    print("🤖 Claudeでレポート生成中...")
    report = generate_report(stocks_data, news_data)

    # 4. コンソール出力（確認用）
    print("\n" + "="*60)
    print(report)
    print("="*60)

    # 5. メール送信（アドレスが設定されている場合）
    if GMAIL_ADDRESS and GMAIL_PASSWORD and TO_ADDRESS:
        send_email(report)
    else:
        print("⚠️ メール設定未完了のため送信スキップ（コンソール出力のみ）")

if __name__ == "__main__":
    main()

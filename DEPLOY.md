# 部署到 Streamlit Cloud — 5 分鐘上線

讓朋友能用永久網址（如 `https://你的名字-alphax.streamlit.app`）打開看板。

## 步驟

### 1. 把專案丟上 GitHub

```bash
cd "C:\Users\charl\我的雲端硬碟\自動化交易分析平台"
git init
git add .
git commit -m "Alpha-X dashboard v1"
# 在 github.com 開個 repo (alpha-x-dashboard)，然後：
git remote add origin https://github.com/你的帳號/alpha-x-dashboard.git
git branch -M main
git push -u origin main
```

⚠️ **記得加 `.gitignore`** 避免把 SQLite、Mock JSON 全推上去：

```
__pycache__/
*.pyc
alpha_x_v2.db
.streamlit/secrets.toml
```

### 2. 跑一次真實回測產生 backtest_results.json

```bash
pip install -r requirements.txt
python3 backtest.py --months 24 --horizon 60
# 大約跑 5-15 分鐘（24 個月 × 100 檔 × 數次 yfinance 呼叫）
```

跑完會產生 `backtest_results.json`，dashboard 會優先讀這個（覆蓋 mock）。

> 💡 **省力做法**：先用 `--months 6` 跑短一點看流程，OK 再跑 24 個月。

### 3. 連動 Streamlit Cloud

1. 到 [https://share.streamlit.io](https://share.streamlit.io) 用 GitHub 登入
2. 點「**New app**」
3. 設定：
   - **Repository**：`你的帳號/alpha-x-dashboard`
   - **Branch**：`main`
   - **Main file path**：`dashboard.py`（給朋友看用這個，不要選 app.py）
4. 點「**Deploy**」

3 分鐘後會拿到永久網址。把它丟給朋友就好。

### 4. 自動化每日刷新（進階）

Streamlit Cloud 不支援 cron，但可以用 **GitHub Actions** 每天跑 backtest 並 push 結果：

```yaml
# .github/workflows/daily_backtest.yml
name: Daily Backtest
on:
  schedule:
    - cron: '0 14 * * *'  # 每天 UTC 14:00 = 台灣 22:00（台股收盤後）
  workflow_dispatch:

jobs:
  run:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v3
      - uses: actions/setup-python@v4
        with: { python-version: '3.10' }
      - run: pip install -r requirements.txt
      - run: python3 backtest.py
      - run: |
          git config user.name "bot"
          git config user.email "bot@github"
          git add backtest_results.json
          git commit -m "Daily backtest update" || echo "no changes"
          git push
```

GitHub Action 跑完 push 新 JSON → Streamlit Cloud 會自動重新部署 → 看板自動更新。

---

## 常見問題

### Q: FinMind 抓不到籌碼怎辦？
A: 預設用匿名額度，每日有上限。建議到 [finmindtrade.com](https://finmindtrade.com) 申請免費 token，存到 `.streamlit/secrets.toml`：

```toml
finmind_token = "你的 token"
```

然後改 `app.py` 的 `DataEngine.get_tw_chips`：
```python
dl = DataLoader()
dl.login_by_token(api_token=st.secrets["finmind_token"])
```

### Q: 看板速度太慢
A: Streamlit Cloud 免費版 1GB 記憶體，跑 100 檔 yfinance 約 30-60 秒。優化方法：
1. 把「今日掃描」改成「讀預先 cache 的 JSON」
2. 在 GitHub Action 順便跑 `today_scan.json`，dashboard 只讀檔不掃描

### Q: 朋友怎麼分辨「這是娛樂」vs「這是建議」？
A: dashboard.py 已內建 4 個免責提示：
- 標題副字「僅供娛樂與研究參考」
- 「機率性陳述、非預測單一價格」
- 「未來函數防護」方法揭露
- 底部黃色警告框 — 過去績效不代表未來、不負盈虧責任

### Q: 我想改成只給特定朋友看（私密）
A: Streamlit Cloud 免費版只支援公開。要私密：
- **Streamlit Cloud Teams** ($20/月，可加 password)
- **Railway / Render** + 自己加 `streamlit-authenticator`
- **本機 + ngrok** 短期分享（連結每次重啟換）

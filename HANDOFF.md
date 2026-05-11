# 📁 Alpha-X Global 自動化分析平台 — 完整交接文件

> **版本**：v2.1
> **更新日期**：2026-05-05
> **作者**：Charlie（Owner）+ Gemini（v0–v1.1 設計）+ Claude（v2.0–v2.1 重構）
> **目的**：讓任何接手者（人或 AI）在 30 分鐘內理解系統現況、設計決策與下一步。

---

## 一、專案總覽

### 1.1 起源
原本是個人「不想每天手動做功課」的痛點，希望有腳本自動篩選符合技術面+籌碼面的股票。
經過討論決定轉型成 **Web App + 雙商業模式**：
1. 免費版 + 廣告/聯盟 → 流量變現
2. 自動化交易（自用，丟一筆錢進去驗證）

### 1.2 核心問題
| # | 問題 | 解法 |
|---|---|---|
| 1 | 排除流動性差的地雷股 | L0 流動性過濾（日均成交額 ≥ 5,000 萬 TWD） |
| 2 | 結合技術面與籌碼面 | 三層漏斗 L0→L1→L2 |
| 3 | 回測難題（無法用過去資料驗證） | 時光機 (Time Machine) + 隔日開盤買進 + 真實 MDD |
| 4 | 跨市場策略一致性 | TW/US/JP 統一邏輯，差異只在 L2 確認層 |
| 5 | 缺乏總經視角（只看個股） | v2.1 大盤環境過濾 (Market Regime) |
| 6 | 不知道個股是強是弱 | v2.1 相對強度 RS（vs 大盤 90 日超額） |

### 1.3 預期成果
跨國（台/美/日股）中長期波段決策系統，使用者一鍵掃描 → 得 L0/L1/L2 評級 → 模擬下單 → 風控追蹤 → 時光機驗證。

---

## 二、雙產品策略

### 2.1 路線圖（從 README 摘要）

| 階段 | 時程 | 目標 | 變現 |
|---|---|---|---|
| **MVP** (目前 v2.1) | 已交付 | Streamlit 可跑、決策邏輯完整、16 測試綠 | — |
| **v2.2 免費 Web** | 1–2 週 | SEO 友善、每股一頁、RWD | Google AdSense + 券商聯盟 |
| **v3.0 Pro 訂閱** | 1–2 月 | 推播、自選股、無限時光機、即時資料 | NT$299/月 |
| **v4.0 自動化交易** | 3–6 月 | 串永豐 Shioaji（自用） | 自我驗證 → 未來授權他人 |

### 2.2 法律注意（重要）
- **替別人下單** = 違反《證券投資顧問事業管理規則》第 70 條（罰 60 萬–300 萬 + 刑責）
- **自己用自己的錢** = 完全合法
- **發訊號 + 用戶用自己 API 下單** = 業界灰色地帶（Composer / QuantConnect 模式）

---

## 三、技術架構

### 3.1 技術選型
- **語言**：Python 3.10+
- **前端**：Streamlit（MVP 快速驗證；之後抽 FastAPI + Next.js）
- **資料來源**：
  - `yfinance`：跨市場股價、基本面、匯率、大盤指數（free, 無 token）
  - `FinMind`：台股投信買賣超（free 匿名額度）
- **儲存**：SQLite（本機 `alpha_x_v2.db`，未來上雲改 Supabase）
- **部署**：Localhost + ngrok（朋友測試）→ Streamlit Cloud / Railway

### 3.2 模組設計

```
[ app.py — 單檔 MVP ]
├── 系統常數 (DB_PATH, MIN_TURNOVER, INDEX_MAP, ...)
├── SourceProvider          # 內建股池清單（dict）
├── DataEngine              # 純資料層
│   ├── get_fx_to_twd()         # 動態匯率 cache 6h
│   ├── get_daily()             # 永遠抓 1d, cache 30min
│   ├── to_weekly()             # Resample W-FRI
│   ├── slice_until()           # 時光機切片
│   ├── get_index_full()        # ★v2.1 大盤指數 cache 4h
│   └── get_tw_chips()          # FinMind 投信 cache 4h
├── GlobalValidator         # 策略大腦
│   ├── validate()              # 主流程
│   ├── _get_market_regime()    # ★v2.1
│   ├── _calc_rs()              # ★v2.1
│   ├── _check_liquidity()
│   ├── _check_trend_weekly/daily()
│   ├── _check_fundamentals()   # 軟性
│   ├── _check_chips_tw()       # FinMind 兩段式
│   └── _check_volume_burst()   # US/JP
├── SQLite 函式群           # init_db / get_cash / execute_trade ...
├── render_radar_tab()      # 戰略雷達 + 時光機 + 法人級回測指標
├── render_lab_tab()        # 模擬實驗室
├── render_risk_tab()       # 風控儀表板
└── main()                  # 三個 tab + sidebar
```

### 3.3 未來架構（v3.0+）

```
[ Core Engine ]  ← 純 Python，無 Streamlit 依賴
       ↓
   ┌───┴────────────────────┐
[Streamlit]          [FastAPI REST]
（免費版 UI）          （Pro 訂閱 / 行動 / 第三方）
                            ↓
                      [Trading Executor]
                      （永豐 Shioaji / Alpaca / IBKR）
```

---

## 四、已完成功能（v2.0 + v2.1）

### 4.1 v2.0 基礎能力（從 Gemini PRD v1.1 重構）
- ✅ 永遠抓日線，Weekly 模式自動 Resample（修正維度錯誤 P0）
- ✅ 全 API 加 `@st.cache_data`（修正限流 P0）
- ✅ 軟性基本面（EPS 缺值不誤殺）
- ✅ FinMind 兩段式：5 日累積買超 + 正買天數
- ✅ 動態匯率（USDTWD/JPYTWD），失敗 fallback
- ✅ UI 結果存 session_state，避免切 Tab 重 call API
- ✅ SQLite 模擬下單：手續費 0.1425%、證交稅 0.3%、超賣保護
- ✅ 風控儀表板：庫存重新驗證、跌破生命線紅字

### 4.2 v2.1 法人視角升級（基於 Claude 提的 Tier 1 建議）
- ✅ **大盤環境過濾**：^TWII/^GSPC/^N225 vs 200D-MA，BEAR 自動把 L2 降為 L1
- ✅ **相對強度 RS**：個股 90 日報酬 - 大盤同期，>0 加分、<-10% 扣分
- ✅ **時光機 lookahead 修正**：模式下跳過基本面評分（避免用現值評過去）
- ✅ **回測買進價改隔日開盤**：避免「當日收盤訊號當日買」的未來函數
- ✅ **法人級回測指標**：CAGR / 真實 MDD（cummax-based）/ 勝率 / 賠率
- ✅ **市場溫度計 UI**：掃描頂部紅綠燈
- ✅ **庫存體檢加 RS 與市場環境欄位**

---

## 五、檔案結構與索引

```
C:\Users\charl\我的雲端硬碟\自動化交易分析平台\
├── app.py              # 主程式（42 KB，1078 行，單檔）
├── README.md           # 使用者文件 + 商業化路線圖
├── requirements.txt    # streamlit / yfinance / FinMind / pandas / numpy
├── HANDOFF.md          # 本文件
├── alpha_x_v2.db       # 執行後產生的 SQLite（gitignore）
└── tests/              # （建議）
    ├── test_app.py     # v2.0 10 個 sanity test
    └── test_v21.py     # v2.1 6 個補測
```

### 5.1 程式碼結構速查（行號約略）

| 行號區間 | 內容 |
|---|---|
| 1–95   | 模組 docstring、import、常數、Streamlit 設定 |
| 98–138 | `SourceProvider` 股池字典 |
| 141–290 | `DataEngine` 資料層 |
| 312–582 | `GlobalValidator` 策略大腦 |
| 588–727 | SQLite 函式 (init_db / execute_trade / calc_costs) |
| 730–745 | UI helper |
| 751–919 | `render_radar_tab` 戰略雷達 |
| 922–975 | `render_lab_tab` 模擬實驗室 |
| 978–1032 | `render_risk_tab` 風控儀表板 |
| 1039–1078 | `main()` 入口 |

---

## 六、演算法與策略邏輯

### 6.1 完整漏斗（v2.1）

```
🌐 大盤環境 (Regime)        — BEAR 強制 cap，L2 降為 L1
   ↓
0️⃣ 流動性                    — 日均成交 ≥ 5,000 萬 TWD（換算後）
   ↓
1️⃣ 趨勢
   • Weekly: Close > 30W-MA AND 30W-MA 斜率向上 (4 週前比較)
   • Daily:  Close > 20D-MA AND Close > 60D-MA
   ↓
1️⃣.5 基本面（軟性，時光機跳過）
   • EPS > 0:           +5
   • Revenue > 0:       +5
   • 兩個都負:           -15
   • 缺資料:             0 (中性)
   ↓
1️⃣.6 RS (Relative Strength)
   • RS ≥ +10%:         +10  🚀
   • RS > 0%:           +5   ✅
   • RS > -10%:         0    ⚪
   • RS ≤ -10%:         -10  ⚠️
   ↓
2️⃣ 確認
   • TW: 投信 5 日淨買 ≥ 200 張 AND 正買天數 ≥ 3
   • US/JP: 當日量 / 10 日均量 ≥ 1.2x
   ↓
🏷️ 評級
   • L2 (強勢):  通過全部 + 確認層
   • L1 (觀察):  通過趨勢但確認層未過
   • L0 (淘汰):  趨勢層失敗
   • ⛔ 風險過高: 流動性失敗
   • N/A:        資料不足或無法判斷
```

### 6.2 時光機 (Time Machine)

| 元素 | 實作 |
|---|---|
| 切片 | `df_daily[df_daily.index <= target_date]` |
| 最少資料 | 60 個交易日（不夠則 N/A） |
| 基本面 | 完全跳過（避免 lookahead bias） |
| RS 大盤對照 | 大盤資料也切到 target_date |
| 回測買進價 | `target_date+1` 的 Open（避免未來函數） |
| 回測指標 | 報酬% / 期間最大漲幅 / 真實 MDD（cummax-based） |
| 整體統計 | CAGR / 平均 MDD / 賠率（平均賺/平均賠） |

### 6.3 評分總表

| 通過層 | 分數 |
|---|---|
| 流動性 | +10 |
| 趨勢 | +30 |
| 基本面（最佳） | +10 |
| RS（最佳） | +10 |
| L2 確認 | +30 |
| **L2 滿分** | **90** |
| BEAR cap | -20 |

---

## 七、測試狀態

```
test_app.py:  10 / 10 PASS  (v2.0 基礎)
test_v21.py:   6 /  6 PASS  (v2.1 法人視角)
─────────────────────────────────
總計:         16 / 16 PASS
```

**測試策略**：在 `import app` 之前用 `sys.modules` 注入 `streamlit / yfinance / FinMind` 的 stub，所以**沒有實裝套件也能跑**，避免外網依賴。

**未涵蓋的測試（待補）**：
- 真實 yfinance 資料的 smoke test（需網路）
- Streamlit UI 互動（需 selenium / playwright）
- FinMind 真實 API token 模式
- 多檔同時掃描的效能基準

---

## 八、待辦清單（給下一位開發者）

### 🔴 v2.2 — 訊號品質升級（半週）
- [ ] **乖離率過濾**：避免追高 > +30%MA 的過熱股
- [ ] **量價結構**：OBV / 量價相關係數，取代「單日爆量」
- [ ] **出場規則**：進場時就決定停損 / 停利 / 追蹤停利三個價位
- [ ] **產業 RS 排名**：把同股池內個股按 RS 排序

### 🟡 v3.0 — 產品力 + 變現（1 週）
- [ ] 把 `Core Engine`（DataEngine + GlobalValidator）抽出 `core/` package
- [ ] FastAPI REST：`/scan`、`/validate/{symbol}`、`/backtest`
- [ ] 股票詳情頁原型：每支股票一個獨立 URL（SEO）
- [ ] SQLite → Supabase（多用戶、雲端持久化）
- [ ] 用戶系統：免費 vs Pro 權限分流

### 🟢 v4.0 — 自動化交易（3–6 月）
- [ ] 永豐 Shioaji 模擬帳號 PoC（訊號 → 下單）
- [ ] 部位管理 (Position Sizing)：每筆最大虧損 ≤ 帳戶 1%
- [ ] 風控守門員：跌破停損強制平倉
- [ ] 對帳系統：每日比對券商實單 vs 系統紀錄

---

## 九、自動化交易：給自己用的合理路徑

### 9.1 法律檢查（自己用）
- ✅ 100% 合法 — 你的帳戶、你的錢、你自己的演算法
- ⚠️ 不能演化成「幫朋友代操」（沒牌照即違法）

### 9.2 真錢上線分階段（強烈建議）

| 階段 | 時間 | 動作 | 風險上限 |
|---|---|---|---|
| **0. 紙上驗證** | 2–3 個月 | 繼續 paper trading + 時光機跑過去 2 年回測 | 0 |
| **1. 小額試水** | 3 個月 | 真錢但 ≤ NT$ 100,000，**手動下單**（系統發訊號） | 1 萬內可承受 |
| **2. 半自動** | 3 個月 | 加碼到 NT$ 500,000，**系統送單前彈窗確認** | 5 萬內可承受 |
| **3. 純自動** | 視情況 | 全自動，但設 daily loss limit（單日虧 > 3% 自動停機） | 視帳戶總額 |

### 9.3 上真錢前的 Hard Checklist

```
[ ] 系統連續穩定運作 ≥ 30 天無 crash
[ ] 時光機回測 2018–2024 含熊市（2018 Q4、2020 Q1、2022 全年）
[ ] 回測 Sharpe ≥ 1.0、MDD ≤ -25%
[ ] 有獨立的「kill switch」：一鍵全平倉
[ ] 每筆下單前計算「最大虧損 / 帳戶總額」≤ 1%
[ ] 所有 API key 存 .env 不入 git
[ ] 永豐 Shioaji token 有過期警告
[ ] 系統異常會發 LINE / Telegram 推播給你
[ ] 對帳腳本：每日收盤後比對「實單成交」vs「系統紀錄」
```

### 9.4 推薦技術棧（自用版）

```python
# 永豐 Shioaji（台股最強 SDK）
import shioaji as sj
api = sj.Shioaji(simulation=True)  # 模擬帳戶先跑 1 個月
api.login(api_key=..., secret_key=...)

# 美股建議 Alpaca（免費 paper trading）
from alpaca.trading.client import TradingClient
client = TradingClient("KEY", "SECRET", paper=True)
```

---

## 十、開發歷程精華對話

### 10.1 v0–v1.1（與 Gemini）
- 「我要真實資料、做網頁版、可以醜但要能變現」 → MVP 用 yfinance + Streamlit
- 「會不會 1000 人看到同樣訊號去買同一檔？」 → 加 Hard Filter 流動性
- 「MA30 應該是 6 週不是 30 週」 → 統一單位語言為 `30W-MA`/`60D-MA`
- 「能接 FinMind 嗎？」 → PRD v1.1 加入 FinMind 投信籌碼
- 「我要還原到過去某一天看半年後績效」 → 定義 Time Machine 回測

### 10.2 v2.0（與 Claude）
- 接手 Gemini 交接文件，發現 P0 問題：FinMind 方法名錯、Weekly 維度錯、EPS 誤殺、匯率寫死、無 Cache
- 全部修掉，重構成 v2.0 單檔 app.py，10 個 sanity test 全綠

### 10.3 v2.1（與 Claude，法人視角）
- 「以法人/投信角度看還缺什麼？」 → Claude 列出 Tier 1：大盤環境、RS、出場、Lookahead
- 「直接動手實作」 → v2.1 完成大盤過濾 + RS + 時光機修正
- 「自動化交易丟錢自己玩可以嗎？」 → 合法、合理，但要分階段（本文件第九節）

---

## 十一、給下一位 AI 的接手指引

### 11.1 第一件事
1. 開 `app.py`，先看頂部 docstring 了解 v2.1 改了什麼
2. 跑 `python3 test_app.py && python3 test_v21.py` 確認綠
3. `streamlit run app.py` 在地端摸過三個 tab

### 11.2 改 code 之前
- 看本文件「第八節 待辦清單」對齊 Owner 的優先順序
- 任何加減「評分數值」都要更新測試
- 別動 `slice_until` 和「時光機跳過基本面」邏輯（lookahead bias 防護）

### 11.3 跟 Owner 對話的風格
- Owner 是技術背景，不要太 fluffy；錯了直接認、用 P0/P1 標籤分優先順序
- 不要假裝「都做好了」；mounted file 寫入有 truncation 風險，務必驗證 byte size

### 11.4 已知技術坑
1. **雲端硬碟 mount 寫入大檔案會被截斷**（中文路徑 + 大檔）
   → 解法：先寫到 `outputs/`（無中文），再 `cp` 到 mount
2. **沙箱無法 pip install**（PyPI 被擋）
   → 用 stub 注入測試（見 test_app.py 開頭）
3. **`yf.download` 偶爾回 MultiIndex columns**
   → 已有 `df.columns.get_level_values(0)` 處理
4. **`yf.Ticker(...).info` 可能 timeout / 回 None**
   → try/except + 軟性處理

---

> 本文件結束。歡迎接手者直接從「第八節 待辦清單」挑優先項目開工。

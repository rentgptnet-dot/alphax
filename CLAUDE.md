# CLAUDE.md

> 這份文件給未來的 Claude session 看，幫你快速上手這個專案。
> Owner：Charlie；目前版本：**v2.3-public-mode**；最後更新：2026-05-12

---

## 1. 一行專案描述

跨市場（台 / 美 / 日股）中長期波段決策系統 — Streamlit 雙 App MVP：
- **app.py**：Owner 自用全功能版（含時光機、模擬下單、風控）
- **dashboard.py**：給朋友看的純看板版（唯讀、純娛樂、可部署 Streamlit Cloud）

Owner 兩條產品線：免費 Web（廣告/聯盟）+ 自用自動化下單。

---

## 2. 開工前必讀

### 2.1 文件閱讀順序
1. **本文件 (CLAUDE.md)** — 你正在讀
2. **HANDOFF.md** — 完整接手文件（深度版）
3. **DEPLOY.md** — 看板部署到 Streamlit Cloud 教學
4. **README.md** — 使用者文件
5. **app.py / dashboard.py 頂部 docstring**

### 2.2 常用指令
```bash
# 跑全部測試
cd tests && python3 test_app.py && python3 test_v21.py
python3 -B test_dashboard.py  # dashboard 相關

# 自用全功能版
streamlit run app.py

# 公開看板版
streamlit run dashboard.py

# 跑歷史回測（產生 backtest_results.json）
python3 backtest.py --months 24 --horizon 60
```

---

## 3. 檔案結構

```
自動化交易分析平台/
├── app.py                       # 主程式 v2.1（自用版，1078 行 / 42 KB）
├── dashboard.py                 # 公開看板（唯讀版，~250 行）
├── backtest.py                  # 歷史回測腳本
├── pools.py                     # 集中管理股池清單（5 個主題）
├── mock_backtest_results.json   # 假數據（demo 用，跑真實 backtest 後會被覆蓋）
├── backtest_results.json        # 真實回測結果（執行 backtest.py 後產生）
├── README.md / HANDOFF.md / DEPLOY.md / CLAUDE.md
├── requirements.txt
├── alpha_x_v2.db                # SQLite（執行 app.py 後自動產生）
└── tests/
    ├── test_app.py              # v2.0 sanity (10 PASS)
    ├── test_v21.py              # v2.1 法人視角 (6 PASS)
    └── test_dashboard.py        # dashboard sanity (5 PASS)
```

### 3.1 app.py 程式碼地圖（v2.1）

| 行號 | 區塊 |
|---|---|
| 1–95 | docstring / import / 常數 / Streamlit 設定 |
| 98–138 | `SourceProvider` |
| 145–308 | `DataEngine`（cache、Resample、時光機、匯率、大盤、FinMind） |
| 316–582 | `GlobalValidator`（regime、RS、流動性、趨勢、基本面、確認） |
| 588–727 | SQLite 函式 |
| 751–919 | `render_radar_tab` 戰略雷達 |
| 922–975 | `render_lab_tab` 模擬實驗室 |
| 978–1032 | `render_risk_tab` 風控儀表板 |
| 1039–1078 | `main()` |

### 3.2 dashboard.py 程式碼地圖

| 區塊 | 內容 |
|---|---|
| `load_backtest()` | 優先讀 backtest_results.json，找不到讀 mock |
| `scan_today()` | 即時掃描台股 ~100 檔，回傳 L2 picks |
| `render_regime_banner()` | 市場溫度計（紅/黃/綠燈） |
| `render_today_section()` | 今日 L2 強勢股表 |
| `render_summary_metrics()` | 4 個關鍵指標（報酬/baseline/alpha/勝率）+ 進階風險 |
| `render_monthly_history()` | 24 個月攤開（無 cherry-pick） |
| `render_method()` | 方法揭露（避免回測幻覺的 5 條原則） |
| `render_disclaimer()` | 黃色免責框 |

### 3.3 pools.py 五大股池

| 股池 | 用途 | 檔數 |
|---|---|---|
| `TW_TOP_50` | 0050 權值（半導體/金融/塑化等） | ~52 |
| `TW_MID_100` | 0051 中型 51-100 | ~46 |
| `AI_SERVER` | AI 伺服器精選 | 12 |
| `HIGH_DIVIDEND` | 高股息 ETF 重疊成分 | 32 |
| `EV_SUPPLY_CHAIN` | 電動車供應鏈 | 13 |
| **去重後 universe** | dashboard 全掃 | **101** |

---

## 4. 架構與關鍵設計

### 4.1 三層選股漏斗（v2.1 完整版）

```
🌐 大盤環境 (Regime)        ← BEAR 強制 cap，L2 降為 L1
   ↓
0️⃣ 流動性 (日均成交額 ≥ 5,000 萬 TWD)
   ↓
1️⃣ 趨勢 (Weekly: 30W-MA + 斜率 / Daily: 20D + 60D)
   ↓
1️⃣.5 基本面 (軟性，時光機跳過避免 lookahead bias)
   ↓
1️⃣.6 RS — 個股 90 日報酬 vs 大盤
   ↓
2️⃣ 確認 (TW: FinMind 投信兩段式 / US/JP: 爆量 1.2x)
   ↓
🏷️ L0 / L1 / L2 評級
```

### 4.2 時光機 / 回測不可破壞的鐵律

| 規則 | 程式位置 |
|---|---|
| 切片：`df[df.index <= target_date]` | `DataEngine.slice_until` |
| 至少 60 個交易日才評級 | `validate()` 開頭 |
| 模式下跳過 yfinance 基本面 | `validate()` 第 408 行 `if is_time_machine` |
| RS 大盤對照也要切片 | `_calc_rs()` 內 `slice_until(df_idx, target_date)` |
| 回測買進價 = target_date 隔日開盤 | `render_radar_tab` 與 `backtest.py` 內 |
| 扣交易成本：滑價 0.3% + 手續費 0.1425% + 賣稅 0.3% | `backtest.net_return_pct` |

⚠️ **改任何 `validate()` 都要重跑全部測試**（test_app + test_v21）。

### 4.3 避免回測幻覺的 8 條原則（深度說明在 backtest.py docstring）

1. 包含熊市（必含 2022 全年）
2. 全部訊號計入（不 cherry-pick）
3. 等權買入（不主觀加碼）
4. 固定持有期 60 日（不停損不停利避免額外自由度）
5. 扣交易成本
6. 對照 baseline（永遠比 0050）
7. 隔日開盤買（無未來函數）
8. 透明攤開（每月詳細）

---

## 5. 開發慣例

### 5.1 改 code 前的 checklist
```
[ ] 看 HANDOFF.md「待辦清單」對齊 Owner 優先序
[ ] 改 validate() 評分數值 → 對應修改 tests
[ ] 改 DataEngine API → stub 也要更新
[ ] 改 dashboard.py 介面 → 確認跟 mock JSON shape 對得上
[ ] 改 pools.py 股池 → 重跑 backtest.py
```

### 5.2 命名與標記
- v2.1 新增功能在程式碼註解標 `# ★ v2.1` 或 `# v2.1`
- L0/L1/L2 評級字串含 emoji（🔥/👀/⛔），用 `str.contains("L2")` 判斷別用 `==`
- 時光機開關 = `target_date is not None`

### 5.3 測試風格
- **不打外網**：用 `sys.modules` 注入 streamlit/yfinance/FinMind stub
- 測試命名：`[Test N] 標題 → assert → print "PASS"`

---

## 6. 已知坑（重要！）

### 6.1 雲端硬碟 mount 寫入大檔被截斷
- **症狀**：用 Edit 改 `C:\Users\...\我的雲端硬碟\...\*.py` 容易在 36 KB 邊界被截在 UTF-8 中文字元中間
- **解法**：寫到 `outputs/` 再用 bash `cp` 過去
- **驗證**：`stat -c %s` + `python3 -m py_compile`

### 6.2 沙箱無法 pip install
- 沙箱 PyPI 被 proxy 擋
- 跑測試請用 `tests/test_*.py`（內建 stub），有 pandas/numpy 即可
- `python3 -B` 跳過 .pyc 寫入（mount 上 pycache 偶爾鎖住）

### 6.3 yfinance 偶發問題
- `yf.download` 有時回 MultiIndex columns → `df.columns.get_level_values(0)`
- `yf.Ticker(...).info` 可能 timeout 或回 None → try/except + 軟性
- TZ：`df.index.tz_localize(None)` 統一去掉

---

## 7. 當前狀態

### 7.1 測試
```
test_app.py:       10/10 PASS  (v2.0 基礎)
test_v21.py:        6/6  PASS  (v2.1 法人視角)
test_dashboard.py:  5/5  PASS  (v2.2 看板 + 回測)
test_public_mode:   4/4  PASS  (v2.3 公開模式匿名化)
─────────────────────────────────────────
總計:              25/25 PASS
```


### 7.2 v2.3 已完成（最新）
- ✅ `dashboard.py` 加 `PUBLIC_MODE` toggle（環境變數 / Streamlit secrets 切換）
- ✅ `pools.py` 加 `SECTOR_MAP`（101 檔產業分類）+ `anonymize_picks()`
- ✅ 公開模式自動匿名化個股（半導體 #1）+ 中性措辭（「篩選結果」取代「強勢」）
- ✅ 加強免責：明確標註「無提供證券投資顧問業務」
- ✅ 加 4 個 PUBLIC_MODE 測試（總計 25/25 PASS）

### 7.2 v2.2 已完成
- ✅ 抽 `pools.py`（5 主題股池，101 檔台股 unique）
- ✅ `dashboard.py` 純看板版（市場溫度計 + 今日 picks + 24 月績效）
- ✅ `backtest.py` 完整回測腳本（24 個月 × 100 檔，扣交易成本，比 baseline）
- ✅ `mock_backtest_results.json`（demo 用，沒真實資料前先跑得起來）
- ✅ DEPLOY.md（Streamlit Cloud 部署 + GitHub Action 自動更新）

### 7.3 v2.1 已完成（先前）
- ✅ 大盤環境過濾、相對強度 RS、時光機 lookahead 修正、法人級回測指標、市場溫度計

### 7.4 待辦（依優先序）

**v2.4 — 看板上線（給朋友看）**
- [ ] 跑真實 backtest.py 產生第一份 backtest_results.json
- [ ] 推 GitHub + 部署 Streamlit Cloud
- [ ] 設 GitHub Action 每日自動更新

**v3.0 — 訊號品質升級**
- [ ] 乖離率過濾（避免追高 +30%MA）
- [ ] 量價結構（OBV / 量價相關係數）
- [ ] 出場規則：進場時就決定停損/停利/追蹤停利

**v3.1 — 產品力**
- [ ] 抽 `core/` package
- [ ] FastAPI REST
- [ ] 股票詳情頁（每股一個 URL，SEO）

**v4.0 — Owner 自用自動化交易（3–6 月）**
- [ ] 永豐 Shioaji 模擬帳號 PoC
- [ ] Position Sizing：每筆最大虧損 ≤ 帳戶 1%
- [ ] Kill switch + daily loss limit
- [ ] 對帳系統

---

## 8. 商業化策略（Owner 已確認）

| 階段 | 模式 | 變現 | 進度 |
|---|---|---|---|
| **MVP** | 已交付 | — | ✅ |
| **v2.2 公開看板** | 看板 + 回測勝率 | 流量養成 | ✅ 程式完成、待部署 |
| **v3.0 免費 Web** | SEO + 廣告 | AdSense + 券商開戶聯盟 | 📋 |
| **v3.x Pro 訂閱** | 鎖即時/無限時光機/推播 | NT$299/月 | 📋 |
| **v4.0 自動化交易** | Owner 自用 + 用戶 API 授權 | 自我驗證 | 📋 |

⚠️ **法律紅線**：替別人下單 = 違反《證券投資顧問事業管理規則》第 70 條。Owner 自用 100% 合法。

---

## 9. 給未來 Claude 的提醒

### 9.1 跟 Owner 對話的調性
- Owner 是技術背景，不要 fluffy；錯了直接認、用 P0/P1 標籤分優先序
- Owner 喜歡被「以法人/投信角度」挑戰他的設計
- 解釋技術術語要用比喻（例如「RS 90D」要先講白話）
- 不要假裝「都做好了」；mounted file 寫入有 truncation 風險，務必驗證 byte size

### 9.2 別動的東西
- `slice_until` 邏輯（時光機）
- 時光機跳過基本面的 if 分支（lookahead bias 防護）
- L0 流動性 hard filter（核心防雷）
- backtest.py 的 8 條防幻覺原則
- SQLite schema（除非加新欄位用 ALTER TABLE migration）

### 9.3 開工的順序
1. 先讀本文件 + HANDOFF.md
2. 跑測試確認 baseline 21/21 PASS
3. 動 code 前先在對話跟 Owner 同步「我打算改 X，影響 Y」
4. 動完跑 `python3 -m py_compile {file}` + `stat -c %s` 確認沒被截斷
5. 跑回歸測試
6. 把改動同步到本文件「當前狀態」與 HANDOFF.md「待辦清單」

---

## 10. 開發歷程速覽

- **v0–v1.1（Gemini）**：初版設計、FinMind PRD、Time Machine 概念
- **v2.0（Claude）**：接手 Gemini，修 P0（Resample 維度、Cache、軟性 EPS、動態匯率），10 測綠
- **v2.1（Claude）**：法人視角 — 大盤過濾 + RS + Lookahead bias 修正 + 法人級回測指標，加 6 測
- **v2.2（Claude）**：拆出 dashboard.py 看板版 + backtest.py 回測腳本 + pools.py 股池管理，加 5 測

完整對話與設計脈絡見 HANDOFF.md 第十節。

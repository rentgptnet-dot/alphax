"""
Sanity test for app.py (Alpha-X Global v2.0)

策略：在 import app 之前，先把 streamlit / yfinance / FinMind 用 stub 注入 sys.modules，
這樣不需要安裝 PyPI 套件也能驗證：
  - 語法正確
  - DataEngine.to_weekly / slice_until 行為正確
  - GlobalValidator 完整流程：流動性 / 趨勢 / 基本面 / 確認
  - SQLite 模擬下單帳目正確
  - 時光機切片不會偷看未來
"""

import os
import sys
import types
import tempfile
from datetime import date, timedelta

import pandas as pd
import numpy as np

# ---------- 1. Stub: streamlit ----------
class _Cache:
    def __call__(self, *a, **kw):
        # 支援 @st.cache_data(ttl=...) 或 @st.cache_data
        if a and callable(a[0]) and not kw:
            return a[0]
        def deco(fn):
            return fn
        return deco

st_stub = types.ModuleType("streamlit")
st_stub.cache_data = _Cache()
st_stub.cache_resource = _Cache()
st_stub.set_page_config = lambda *a, **kw: None
sys.modules["streamlit"] = st_stub

# ---------- 2. Stub: yfinance ----------
yf_stub = types.ModuleType("yfinance")

# 我們會把 mock 資料注入到全域，再讓 yf.download 從這裡讀
_MOCK = {"frames": {}, "fx": {}, "info": {}, "chips": {}, "indices": {}}

def _yf_download(symbol, start=None, end=None, period=None, interval=None,
                 progress=False, auto_adjust=False, **kw):
    # 匯率：USDTWD=X / JPYTWD=X
    if symbol in _MOCK["fx"]:
        rate = _MOCK["fx"][symbol]
        idx = pd.date_range(end=date.today(), periods=5, freq="D")
        return pd.DataFrame({"Close": [rate] * 5}, index=idx)
    # 大盤指數（v2.1）：^TWII / ^GSPC / ^N225
    if symbol in _MOCK.get("indices", {}):
        return _MOCK["indices"][symbol].copy()
    if symbol in _MOCK["frames"]:
        return _MOCK["frames"][symbol].copy()
    return pd.DataFrame()

class _Ticker:
    def __init__(self, symbol):
        self.symbol = symbol
    @property
    def info(self):
        return _MOCK.get("info", {}).get(self.symbol, {})

yf_stub.download = _yf_download
yf_stub.Ticker = _Ticker
sys.modules["yfinance"] = yf_stub

# ---------- 3. Stub: FinMind ----------
finmind_pkg = types.ModuleType("FinMind")
finmind_data = types.ModuleType("FinMind.data")

class _DataLoader:
    def taiwan_stock_institutional_investors(self, stock_id=None, start_date=None, end_date=None):
        return _MOCK.get("chips", {}).get(stock_id, pd.DataFrame())

finmind_data.DataLoader = _DataLoader
finmind_pkg.data = finmind_data
sys.modules["FinMind"] = finmind_pkg
sys.modules["FinMind.data"] = finmind_data

# ---------- 4. 把 DB 換成暫存路徑，避免污染雲端硬碟 ----------
TMPDIR = tempfile.mkdtemp(prefix="alphax_test_")
os.chdir(TMPDIR)

# ---------- 5. import app ----------
sys.path.insert(0, "/sessions/sharp-zealous-newton/mnt/自動化交易分析平台")
import app  # noqa: E402

print("import app OK, FINMIND_AVAILABLE =", app.FINMIND_AVAILABLE)
print("DB path =", app.DB_PATH)

# ============================================================
# 工具：產生假股價資料
# ============================================================

def make_fake_daily(days=400, start_price=100.0, drift=0.001, vol_per_day=200_000):
    """穩定上漲 + 大量。"""
    idx = pd.date_range(end=date.today(), periods=days, freq="B")  # business days
    rng = np.random.default_rng(42)
    rets = rng.normal(drift, 0.012, size=days)
    close = start_price * np.cumprod(1 + rets)
    high = close * (1 + np.abs(rng.normal(0, 0.01, size=days)))
    low = close * (1 - np.abs(rng.normal(0, 0.01, size=days)))
    op = close * (1 + rng.normal(0, 0.005, size=days))
    vol = np.full(days, vol_per_day, dtype=float) + rng.normal(0, vol_per_day * 0.1, size=days)
    df = pd.DataFrame({"Open": op, "High": high, "Low": low, "Close": close, "Volume": vol},
                      index=idx)
    return df


def make_fake_chips(n=10, net_lots_per_day=80):
    """產生 FinMind 投信買賣超假資料（單位：股）。"""
    idx = pd.date_range(end=date.today(), periods=n, freq="B")
    rows = []
    for d in idx:
        rows.append({"date": d.strftime("%Y-%m-%d"), "name": "Investment_Trust",
                     "buy": net_lots_per_day * 1000, "sell": 0})
    return pd.DataFrame(rows)


# ============================================================
# 測試 1：DataEngine.to_weekly
# ============================================================

print("\n[Test 1] DataEngine.to_weekly")
df_d = make_fake_daily(days=200)
df_w = app.DataEngine.to_weekly(df_d)
assert not df_w.empty, "weekly should not be empty"
assert {"Open", "High", "Low", "Close", "Volume"}.issubset(df_w.columns)
# 檢查 weekly volume 約等於該週日線 volume 加總
assert df_w["Volume"].iloc[-2] > df_d["Volume"].iloc[-1], "weekly vol should be sum"
print(f"  daily rows={len(df_d)}, weekly rows={len(df_w)} OK")


# ============================================================
# 測試 2：DataEngine.slice_until（時光機切片）
# ============================================================

print("\n[Test 2] DataEngine.slice_until")
mid = df_d.index[100].date()
sliced = app.DataEngine.slice_until(df_d, mid)
assert sliced.index.max().date() <= mid, "should not contain future bars"
assert len(sliced) == 101
print(f"  total={len(df_d)}, sliced@{mid}={len(sliced)} OK (no future leak)")


# ============================================================
# 測試 3：GlobalValidator 完整流程（台股 L2，籌碼通過）
# ============================================================

print("\n[Test 3] Validator end-to-end (TW, expecting L2)")
_MOCK["frames"]["2330.TW"] = make_fake_daily(days=500, start_price=500, drift=0.0015,
                                              vol_per_day=20_000_000)
_MOCK["info"]["2330.TW"] = {"trailingEps": 35.0, "revenueGrowth": 0.18}
_MOCK["fx"]["USDTWD=X"] = 32.0
_MOCK["fx"]["JPYTWD=X"] = 0.21
_MOCK["chips"]["2330"] = make_fake_chips(n=8, net_lots_per_day=80)  # 5 日 = 400 張

v = app.GlobalValidator()
res = v.validate("2330", "TW", "Weekly")
print(f"  symbol={res.symbol}, label={res.label}, score={res.score}")
print(f"  reasons: {res.reasons}")
assert "L2" in res.label, f"expected L2 強勢, got {res.label}"
print("  -> PASS")


# ============================================================
# 測試 4：流動性過濾（量太小 -> 風險過高）
# ============================================================

print("\n[Test 4] Liquidity filter (low volume -> 風險過高)")
_MOCK["frames"]["8999.TW"] = make_fake_daily(days=200, start_price=10, vol_per_day=100_000)
res2 = v.validate("8999", "TW", "Weekly")
print(f"  label={res2.label}")
assert "風險過高" in res2.label
print("  -> PASS")


# ============================================================
# 測試 5：時光機 — target_date 切片後資料不足會拒絕
# ============================================================

print("\n[Test 5] Time Machine slice + insufficient data")
old_date = (date.today() - timedelta(days=365 * 2 + 30))  # 早於我們的假資料起點
# 用較短的資料來模擬「該日前資料不足」
_MOCK["frames"]["TEST.TW"] = make_fake_daily(days=80, start_price=100, vol_per_day=10_000_000)
res3 = v.validate("TEST", "TW", "Weekly", target_date=date.today() - timedelta(days=300))
print(f"  label={res3.label}, reasons={res3.reasons}")
# 應該因為切片後不足 60 根而 N/A
assert res3.label == "N/A"
print("  -> PASS")


# ============================================================
# 測試 6：時光機正常情況（夠資料）
# ============================================================

print("\n[Test 6] Time Machine normal case")
res4 = v.validate("2330", "TW", "Daily", target_date=date.today() - timedelta(days=180))
print(f"  symbol={res4.symbol}, label={res4.label}, target_date={res4.target_date}")
assert res4.target_date is not None
assert res4.current_price > 0
# 確認價格不等於今日最後一筆（因為時光機切回過去）
today_res = v.validate("2330", "TW", "Daily")
assert res4.current_price != today_res.current_price, "time machine 應該回到過去的價格"
print(f"  past price={res4.current_price:.2f}, today price={today_res.current_price:.2f} -> 不同 ✓")
print("  -> PASS")


# ============================================================
# 測試 7：美股爆量分支
# ============================================================

print("\n[Test 7] US market volume burst confirmation")
df_us = make_fake_daily(days=200, start_price=200, vol_per_day=5_000_000)
# 把最後一根的 volume 拉爆 2x
df_us.loc[df_us.index[-1], "Volume"] = df_us["Volume"].iloc[-11:-1].mean() * 2.5
_MOCK["frames"]["NVDA"] = df_us
_MOCK["info"]["NVDA"] = {"trailingEps": 5.0, "revenueGrowth": 0.30}
res5 = v.validate("NVDA", "US", "Daily")
print(f"  label={res5.label}, reasons[-1]={res5.reasons[-1]}")
assert "L2" in res5.label
print("  -> PASS")


# ============================================================
# 測試 8：基本面軟性過濾 — 無資料應該不殺
# ============================================================

print("\n[Test 8] Soft fundamentals (None -> neutral, not killed)")
_MOCK["info"]["NVDA"] = {}  # 無資料
res6 = v.validate("NVDA", "US", "Daily")
print(f"  label={res6.label}")
assert res6.label != "L0 (淘汰)"  # 不應因基本面缺資料被殺
print("  -> PASS")


# ============================================================
# 測試 9：SQLite 模擬下單（買 -> 賣）+ 手續費
# ============================================================

print("\n[Test 9] SQLite trade flow with fees")
app.init_db()  # 確保 table 存在
init_cash = app.get_cash()
ok, msg = app.execute_trade("2330", "TW", "BUY", 1000, 600.0, "test buy")
assert ok, f"buy failed: {msg}"
after_buy = app.get_cash()
expected_buy_cost = 1000 * 600 + max(20.0, 1000 * 600 * app.TW_FEE_RATE)
assert abs((init_cash - after_buy) - expected_buy_cost) < 0.5, \
    f"cash diff {init_cash - after_buy} != expected {expected_buy_cost}"
print(f"  buy 1000@600: cash {init_cash:.0f} -> {after_buy:.0f} (扣 {init_cash-after_buy:.2f})")

ok, msg = app.execute_trade("2330", "TW", "SELL", 500, 650.0, "test sell")
assert ok, f"sell failed: {msg}"
after_sell = app.get_cash()
sell_gross = 500 * 650
sell_fee = max(20.0, sell_gross * app.TW_FEE_RATE)
sell_tax = sell_gross * app.TW_TAX_RATE
expected_sell_in = sell_gross - sell_fee - sell_tax
assert abs((after_sell - after_buy) - expected_sell_in) < 0.5
print(f"  sell 500@650: cash +{after_sell-after_buy:.2f} (預期 {expected_sell_in:.2f})")

port = app.get_portfolio_df()
assert len(port) == 1 and port.iloc[0]["amount"] == 500
print(f"  剩餘庫存 {port.iloc[0]['amount']} 股")

# 賣超量應該被擋
ok, msg = app.execute_trade("2330", "TW", "SELL", 9999, 650.0)
assert not ok and "庫存不足" in msg
print(f"  超賣保護 OK: {msg}")
print("  -> PASS")


# ============================================================
# 測試 10：美/日股不收稅，台股才收
# ============================================================

print("\n[Test 10] US/JP markets have no fees in this MVP")
fee, tax = app.calc_costs("US", "SELL", 10, 800)
assert fee == 0.0 and tax == 0.0
fee_tw, tax_tw = app.calc_costs("TW", "SELL", 1000, 100)
assert fee_tw > 0 and tax_tw > 0
print(f"  US sell -> fee={fee}, tax={tax}; TW sell -> fee={fee_tw}, tax={tax_tw}")
print("  -> PASS")


# ============================================================
# v2.1 測試
# =============================================
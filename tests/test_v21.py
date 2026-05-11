"""v2.1 補充測試：大盤環境 / RS / 時光機 lookahead 修正"""
import os, sys, types, tempfile
from datetime import date, timedelta
import pandas as pd
import numpy as np

# Stubs (same as test_app.py)
class _Cache:
    def __call__(self, *a, **kw):
        if a and callable(a[0]) and not kw:
            return a[0]
        return lambda fn: fn

st_stub = types.ModuleType("streamlit")
st_stub.cache_data = _Cache()
st_stub.cache_resource = _Cache()
st_stub.set_page_config = lambda *a, **kw: None
sys.modules["streamlit"] = st_stub

yf_stub = types.ModuleType("yfinance")
_MOCK = {"frames": {}, "fx": {}, "info": {}, "chips": {}, "indices": {}}

def _yf_download(symbol, start=None, end=None, period=None, interval=None,
                 progress=False, auto_adjust=False, **kw):
    if symbol in _MOCK["fx"]:
        rate = _MOCK["fx"][symbol]
        idx = pd.date_range(end=date.today(), periods=5, freq="D")
        return pd.DataFrame({"Close": [rate] * 5}, index=idx)
    if symbol in _MOCK["indices"]:
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

finmind_pkg = types.ModuleType("FinMind")
finmind_data = types.ModuleType("FinMind.data")
class _DataLoader:
    def taiwan_stock_institutional_investors(self, stock_id=None,
                                              start_date=None, end_date=None):
        return _MOCK.get("chips", {}).get(stock_id, pd.DataFrame())
finmind_data.DataLoader = _DataLoader
finmind_pkg.data = finmind_data
sys.modules["FinMind"] = finmind_pkg
sys.modules["FinMind.data"] = finmind_data

os.chdir(tempfile.mkdtemp(prefix="alphax_v21_"))
sys.path.insert(0, "/sessions/sharp-zealous-newton/mnt/自動化交易分析平台")
import app


def fake_stock(days=400, start_price=100.0, drift=0.001, vol=10_000_000, seed=42):
    idx = pd.date_range(end=date.today(), periods=days, freq="B")
    rng = np.random.default_rng(seed)
    rets = rng.normal(drift, 0.012, size=days)
    close = start_price * np.cumprod(1 + rets)
    return pd.DataFrame({
        "Open": close * (1 + rng.normal(0, 0.005, size=days)),
        "High": close * (1 + np.abs(rng.normal(0, 0.01, size=days))),
        "Low":  close * (1 - np.abs(rng.normal(0, 0.01, size=days))),
        "Close": close,
        "Volume": np.full(days, vol, dtype=float),
    }, index=idx)


def fake_index(days=400, start=15000, drift=0.0005, seed=7):
    idx = pd.date_range(end=date.today(), periods=days, freq="B")
    rng = np.random.default_rng(seed)
    rets = rng.normal(drift, 0.008, size=days)
    close = start * np.cumprod(1 + rets)
    return pd.DataFrame({"Open": close, "High": close * 1.005,
                         "Low": close * 0.995, "Close": close,
                         "Volume": np.full(days, 1e9)}, index=idx)


def fake_chips(n=8, lots=100):
    idx = pd.date_range(end=date.today(), periods=n, freq="B")
    return pd.DataFrame([{"date": d.strftime("%Y-%m-%d"),
                          "name": "Investment_Trust",
                          "buy": lots * 1000, "sell": 0} for d in idx])


_MOCK["fx"]["USDTWD=X"] = 32.0
_MOCK["fx"]["JPYTWD=X"] = 0.21
v = app.GlobalValidator()


# ============================================================
# Test 11：BULL — L2 不降級
# ============================================================

print("\n[Test 11] Market Regime BULL")
_MOCK["indices"]["^TWII"] = fake_index(days=400, start=15000, drift=0.0008)
_MOCK["frames"]["2330.TW"] = fake_stock(days=500, start_price=500,
                                         drift=0.0015, vol=20_000_000)
_MOCK["info"]["2330.TW"] = {"trailingEps": 35, "revenueGrowth": 0.18}
_MOCK["chips"]["2330"] = fake_chips(n=8, lots=80)

res = v.validate("2330", "TW", "Weekly")
print(f"  regime={res.extras.get('市場環境')}, label={res.label}")
assert res.extras["市場環境"] == "BULL"
assert "L2" in res.label
print("  PASS")


# ============================================================
# Test 12：BEAR — L2 自動降為 L1
# ============================================================

print("\n[Test 12] Market Regime BEAR (L2 -> L1 cap)")
days = 400
idx = pd.date_range(end=date.today(), periods=days, freq="B")
rng = np.random.default_rng(11)
rets = np.concatenate([
    rng.normal(0.002, 0.008, 200),
    rng.normal(-0.003, 0.012, 200),
])
close = 15000 * np.cumprod(1 + rets)
_MOCK["indices"]["^TWII"] = pd.DataFrame({
    "Open": close, "High": close * 1.005, "Low": close * 0.995,
    "Close": close, "Volume": np.full(days, 1e9)}, index=idx)

res = v.validate("2330", "TW", "Weekly")
print(f"  regime={res.extras.get('市場環境')}, label={res.label}")
assert res.extras["市場環境"] == "BEAR", f"expected BEAR, got {res.extras.get('市場環境')}"
assert "L2" not in res.label
assert "熊市降級" in res.label
print("  PASS")


# ============================================================
# Test 13：RS — 個股贏大盤
# ============================================================

print("\n[Test 13] Relative Strength")
_MOCK["indices"]["^TWII"] = fake_index(days=400, start=15000, drift=0.0003)
_MOCK["frames"]["3697.TW"] = fake_stock(days=300, start_price=200,
                                         drift=0.005, vol=10_000_000)
_MOCK["info"]["3697.TW"] = {"trailingEps": 10, "revenueGrowth": 0.5}
_MOCK["chips"]["3697"] = fake_chips(n=8, lots=100)
res = v.validate("3697", "TW", "Daily")
rs = res.extras.get("RS_90D_%")
print(f"  RS_90D={rs}%, label={res.label}")
assert rs is not None and rs > 0
print("  PASS")


# ============================================================
# Test 14：時光機跳過基本面（lookahead 修正）
# ============================================================

print("\n[Test 14] Time Machine skips fundamentals")
res = v.validate("2330", "TW", "Weekly",
                 target_date=date.today() - timedelta(days=180))
joined = " ".join(res.reasons)
assert "時光機模式：基本面已略過" in joined, f"reasons={res.reasons}"
assert "✅ 基本面" not in joined
print("  PASS")


# ============================================================
# Test 15：時光機的 RS 用切片資料計算（vs 今日 RS 應不同）
# ============================================================

print("\n[Test 15] Time Machine RS uses sliced data")
res_today = v.validate("3697", "TW", "Daily")
res_past = v.validate("3697", "TW", "Daily",
                       target_date=date.today() - timedelta(days=200))
print(f"  RS today={res_today.extras.get('RS_90D_%')}%, "
      f"RS @ -200d={res_past.extras.get('RS_90D_%')}%")
assert res_today.extras.get("RS_90D_%") != res_past.extras.get("RS_90D_%")
print("  PASS")


# ============================================================
# Test 16：UNKNOWN regime（大盤資料缺失）— 不影響評級
# ============================================================

print("\n[Test 16] UNKNOWN regime (大盤缺失)")
_MOCK["indices"].pop("^GSPC", None)  # 確保美股大盤無資料
_MOCK["frames"]["NVDA"] = fake_stock(days=200, start_price=200,
                                      drift=0.002, vol=5_000_000)
df_nvda = _MOCK["frames"]["NVDA"]
df_nvda.loc[df_nvda.index[-1], "Volume"] = df_nvda["Volume"].iloc[-11:-1].mean() * 2.5
_MOCK["info"]["NVDA"] = {"trailingEps": 5, "revenueGrowth": 0.3}
res = v.validate("NVDA", "US", "Daily")
print(f"  regime={res.extras.get('市場環境')}, label={res.label}")
assert res.extras["市場環境"] == "UNKNOWN"
assert "L2" in res.label  # UNKNOWN 不降級
print("  PASS")


print("\n========================================")
print("All v2.1 tests passed!")
print("========================================")

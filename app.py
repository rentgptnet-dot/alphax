"""
Alpha-X Global v2.1
====================
跨市場（台 / 美 / 日）中長期波段決策系統 + 時光機回測 + 模擬交易與風控

v2.1 法人視角升級：
  ★ 大盤環境過濾 (Market Regime)：^TWII / ^GSPC / ^N225 vs 200D-MA
    - BULL 多頭 → 正常給 L2
    - NEUTRAL 盤整 → 正常但提醒
    - BEAR 空頭 → 全部降級為觀察 (避免逆勢買進)
  ★ 相對強度 RS (Relative Strength)：個股 90 日報酬 vs 大盤超額
    - > 0% 加 5 分 (跑贏大盤)
    - < -10% 扣 10 分 (明顯弱勢)
  ★ 時光機 Lookahead Bias 修正：
    - 模式下跳過基本面評分 (yfinance.info 是現值，不是當時值)
    - 回測買進價改用「target_date 隔日開盤價」(真實可成交)

v2.0 基礎能力：
  1. 永遠抓日線，Weekly 模式 Resample 為週線
  2. Cache、軟性基本面、FinMind 兩段式確認
  3. 動態匯率 (USDTWD / JPYTWD)
  4. UI 狀態用 st.session_state，避免重 call API
"""

from __future__ import annotations

import sqlite3
import time
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from typing import Optional

import numpy as np
import pandas as pd
import streamlit as st
import yfinance as yf

# FinMind 是 optional（沒裝也能跑，台股籌碼會降級成「資料不可用」）
try:
    from FinMind.data import DataLoader  # type: ignore

    FINMIND_AVAILABLE = True
except Exception:
    FINMIND_AVAILABLE = False


# ============================================================
# 系統常數
# ============================================================

DB_PATH = "alpha_x_v2.db"
INITIAL_CASH = 3_000_000  # 初始模擬資金 (TWD)

# 流動性門檻：日均成交額（TWD）
MIN_DAILY_TURNOVER_TWD = 50_000_000  # 5,000 萬

# 台股交易成本
TW_FEE_RATE = 0.001425  # 手續費 0.1425%
TW_TAX_RATE = 0.003     # 證交稅 0.3%（賣出才收）
TW_FEE_DISCOUNT = 1.0   # 折扣 (1.0 = 不打折)

# 預設動態抓資料的天數（足夠 30W-MA 計算與時光機回顧）
HIST_DAYS_DEFAULT = 365 * 3  # 3 年

# FinMind 投信買超門檻（張）
FINMIND_NET_BUY_THRESHOLD = 100
FINMIND_POSITIVE_DAYS_REQUIRED = 2
FINMIND_LOOKBACK_DAYS = 5

# v2.1：大盤指數對應
INDEX_MAP = {
    "TW": "^TWII",   # 台灣加權指數
    "US": "^GSPC",   # S&P 500
    "JP": "^N225",   # 日經 225
}
REGIME_MA_DAYS = 200   # 大盤多空分水嶺
REGIME_BUFFER = 0.02   # ±2% 為盤整區

# v2.1：相對強度 RS 視窗
RS_LOOKBACK_DAYS = 90


# ============================================================
# Streamlit 全域設定
# ============================================================

st.set_page_config(
    page_title="Alpha-X Global v2.1",
    layout="wide",
    page_icon="📈",
)


# ============================================================
# 1. SourceProvider：內建股池清單
# ============================================================

class SourceProvider:
    """內建懶人包股池。未來可改成讀取 Google Sheets / DB。"""

    POOLS: dict[str, dict] = {
        "🇹🇼 台股 - 0050 權值核心": {
            "market": "TW",
            "symbols": ["2330", "2317", "2454", "2308", "2382", "2891", "2412", "2603"],
        },
        "🇹🇼 台股 - AI / 伺服器": {
            "market": "TW",
            "symbols": ["2330", "2382", "3231", "3017", "2376", "6669", "3035", "2345"],
        },
        "🇹🇼 台股 - 高股息熱門": {
            "market": "TW",
            "symbols": ["2884", "2891", "2885", "2886", "2892", "5876", "2880", "2887"],
        },
        "🇺🇸 美股 - Mag7": {
            "market": "US",
            "symbols": ["NVDA", "AAPL", "MSFT", "GOOGL", "META", "AMZN", "TSLA"],
        },
        "🇺🇸 美股 - 半導體": {
            "market": "US",
            "symbols": ["NVDA", "AMD", "AVGO", "TSM", "ASML", "QCOM", "MU", "MRVL"],
        },
        "🇯🇵 日股 - 巴菲特商社": {
            "market": "JP",
            "symbols": ["8001.T", "8002.T", "8031.T", "8053.T", "8058.T"],
        },
        "🇯🇵 日股 - 半導體 / 科技": {
            "market": "JP",
            "symbols": ["8035.T", "6857.T", "6920.T", "9984.T"],
        },
    }

    @classmethod
    def list_pools(cls) -> list[str]:
        return list(cls.POOLS.keys())

    @classmethod
    def get(cls, pool_name: str) -> dict:
        return cls.POOLS[pool_name]


# ============================================================
# 2. DataEngine：抓資料、處理快取、Resample、匯率
# ============================================================

class DataEngine:
    """資料層。所有外部 API 呼叫都在這裡，並有 Streamlit cache。"""

    @staticmethod
    @st.cache_data(ttl=60 * 60 * 6, show_spinner=False)
    def get_fx_to_twd(market: str) -> float:
        """回傳『1 單位該市場貨幣 = 多少 TWD』。失敗則 fallback。"""
        if market == "TW":
            return 1.0
        symbol_map = {"US": "USDTWD=X", "JP": "JPYTWD=X"}
        sym = symbol_map.get(market)
        if sym is None:
            return 1.0
        try:
            fx = yf.download(sym, period="5d", interval="1d",
                             progress=False, auto_adjust=False)
            if isinstance(fx.columns, pd.MultiIndex):
                fx.columns = fx.columns.get_level_values(0)
            if not fx.empty and "Close" in fx.columns:
                rate = float(fx["Close"].dropna().iloc[-1])
                if rate > 0:
                    return rate
        except Exception:
            pass
        return {"US": 32.0, "JP": 0.21}.get(market, 1.0)

    @staticmethod
    @st.cache_data(ttl=60 * 30, show_spinner=False)
    def get_daily(symbol: str, market: str,
                  hist_days: int = HIST_DAYS_DEFAULT) -> tuple[pd.DataFrame, dict, str]:
        """統一抓日線資料。回傳 (df, info, real_symbol)。"""
        s = symbol.strip()
        if market == "TW" and "." not in s:
            suffixes = [".TW", ".TWO"]
        elif market == "JP" and "." not in s:
            suffixes = [".T"]
        else:
            suffixes = [""]

        end = datetime.utcnow().date()
        start = end - timedelta(days=hist_days)

        for suf in suffixes:
            target = f"{s}{suf}"
            try:
                df = yf.download(
                    target,
                    start=start.isoformat(),
                    end=(end + timedelta(days=1)).isoformat(),
                    interval="1d",
                    progress=False,
                    auto_adjust=True,
                )
                if isinstance(df.columns, pd.MultiIndex):
                    df.columns = df.columns.get_level_values(0)
                if df.empty:
                    continue
                if df.index.tz is not None:
                    df.index = df.index.tz_localize(None)
                info = {}
                try:
                    info = yf.Ticker(target).info or {}
                except Exception:
                    info = {}
                return df, info, target
            except Exception:
                continue
        return pd.DataFrame(), {}, ""

    @staticmethod
    def to_weekly(df: pd.DataFrame) -> pd.DataFrame:
        if df.empty:
            return df
        agg = {"Open": "first", "High": "max", "Low": "min",
               "Close": "last", "Volume": "sum"}
        cols = {k: v for k, v in agg.items() if k in df.columns}
        return df.resample("W-FRI").agg(cols).dropna(how="any")

    @staticmethod
    def slice_until(df: pd.DataFrame, target_date: Optional[date]) -> pd.DataFrame:
        if df.empty or target_date is None:
            return df
        return df[df.index <= pd.Timestamp(target_date)]

    # v2.1 大盤指數
    @staticmethod
    @st.cache_data(ttl=60 * 60 * 4, show_spinner=False)
    def get_index_full(market: str) -> pd.DataFrame:
        """抓對應市場大盤指數的 5 年日線。一個市場只 cache 一次。"""
        sym = INDEX_MAP.get(market)
        if sym is None:
            return pd.DataFrame()
        try:
            df = yf.download(sym, period="5y", interval="1d",
                             progress=False, auto_adjust=True)
            if isinstance(df.columns, pd.MultiIndex):
                df.columns = df.columns.get_level_values(0)
            if df.empty:
                return pd.DataFrame()
            if df.index.tz is not None:
                df.index = df.index.tz_localize(None)
            return df
        except Exception:
            return pd.DataFrame()

    @staticmethod
    @st.cache_data(ttl=60 * 60 * 4, show_spinner=False)
    def get_tw_chips(stock_id: str, end_date: date) -> dict:
        """抓 end_date 為止往前 30 天的投信買賣超。"""
        if not FINMIND_AVAILABLE:
            return {"available": False, "net_buy_lots": 0, "positive_days": 0,
                    "msg": "FinMind 未安裝"}
        try:
            clean_id = stock_id.split(".")[0]
            start = (end_date - timedelta(days=30)).isoformat()
            end_s = end_date.isoformat()
            dl = DataLoader()
            df = dl.taiwan_stock_institutional_investors(
                stock_id=clean_id, start_date=start, end_date=end_s,
            )
            if df is None or df.empty:
                return {"available": False, "net_buy_lots": 0, "positive_days": 0,
                        "msg": "FinMind 無資料"}
            it = df[df["name"] == "Investment_Trust"].copy()
            if it.empty:
                return {"available": True, "net_buy_lots": 0, "positive_days": 0,
                        "msg": "無投信進出"}
            it["date"] = pd.to_datetime(it["date"])
            it = it.sort_values("date").tail(FINMIND_LOOKBACK_DAYS)
            it["net"] = it["buy"] - it["sell"]
            net_lots = int(it["net"].sum() / 1000)
            pos_days = int((it["net"] > 0).sum())
            return {"available": True, "net_buy_lots": net_lots,
                    "positive_days": pos_days, "msg": "ok"}
        except Exception as e:
            return {"available": False, "net_buy_lots": 0, "positive_days": 0,
                    "msg": f"FinMind error: {type(e).__name__}"}


# ============================================================
# 3. GlobalValidator：策略大腦
# ============================================================

@dataclass
class ValidationResult:
    symbol: str
    label: str
    score: int
    current_price: float
    target_date: Optional[date]
    market: str
    reasons: list[str] = field(default_factory=list)
    extras: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        d = {
            "代號": self.symbol,
            "市場": self.market,
            "評級": self.label,
            "分數": self.score,
            "價格": round(self.current_price, 2) if self.current_price else None,
            "判斷日": self.target_date.isoformat() if self.target_date else "今日",
            "RS_90D_%": self.extras.get("RS_90D_%"),
            "理由": " / ".join(self.reasons),
        }
        return d


class GlobalValidator:
    def __init__(self, engine: Optional[DataEngine] = None):
        self.engine = engine or DataEngine()

    def validate(
        self,
        symbol: str,
        market: str,
        strategy_mode: str,
        target_date: Optional[date] = None,
    ) -> ValidationResult:

        df_daily, info, real = self.engine.get_daily(symbol, market)
        if df_daily.empty:
            return ValidationResult(symbol=symbol, market=market,
                                    label="N/A", score=0,
                                    current_price=0.0, target_date=target_date,
                                    reasons=["⚠️ 查無資料"])

        df_daily_t = self.engine.slice_until(df_daily, target_date)
        if df_daily_t.empty or len(df_daily_t) < 60:
            return ValidationResult(symbol=real or symbol, market=market,
                                    label="N/A", score=0,
                                    current_price=0.0, target_date=target_date,
                                    reasons=["⚠️ 該日期前資料不足（至少需 60 個交易日）"])

        current_price = float(df_daily_t["Close"].iloc[-1])
        eval_date = df_daily_t.index[-1].date()
        is_time_machine = target_date is not None

        result = ValidationResult(
            symbol=real or symbol, market=market,
            label="L0 (淘汰)", score=0,
            current_price=current_price, target_date=target_date or eval_date,
        )

        # v2.1 第 -1 層：大盤環境
        regime, regime_msg = self._get_market_regime(market, target_date)
        result.extras["市場環境"] = regime
        result.reasons.append(regime_msg)

        # 第 0 層：流動性
        ok_liq, liq_msg, turnover_twd = self._check_liquidity(df_daily_t, market)
        result.extras["日均成交額_TWD"] = int(turnover_twd)
        if not ok_liq:
            result.label = "⛔ 風險過高"
            result.reasons.append(liq_msg)
            return result
        result.reasons.append(liq_msg)
        result.score += 10

        # 第 1 層：趨勢
        if strategy_mode == "Weekly":
            df_for_trend = self.engine.to_weekly(df_daily_t)
            ok_trend, trend_msg, trend_extras = self._check_trend_weekly(df_for_trend, current_price)
        else:
            ok_trend, trend_msg, trend_extras = self._check_trend_daily(df_daily_t, current_price)
        result.extras.update(trend_extras)
        result.reasons.append(trend_msg)
        if not ok_trend:
            result.label = "L0 (淘汰)"
            return result
        result.score += 30

        # v2.1 基本面 — 時光機跳過
        if is_time_machine:
            result.reasons.append("⏰ 時光機模式：基本面已略過（避免 lookahead bias）")
        else:
            fund_msg, fund_delta = self._check_fundamentals(info)
            result.reasons.append(fund_msg)
            result.score += fund_delta

        # v2.1 第 1.5 層：相對強度 RS
        rs_value, rs_msg, rs_delta = self._calc_rs(df_daily_t, market, target_date)
        if rs_value is not None:
            result.extras["RS_90D_%"] = round(rs_value, 2)
        result.reasons.append(rs_msg)
        result.score += rs_delta

        # 第 2 層：確認（台股優先看籌碼，FinMind 失敗則 fallback 用爆量）
        if market == "TW":
            conf_ok, conf_msg = self._check_chips_tw(real or symbol, eval_date)
            if not conf_ok and ("不可用" in conf_msg or "未安裝" in conf_msg
                                or "FinMind error" in conf_msg or "無資料" in conf_msg):
                vol_ok, vol_msg = self._check_volume_burst(df_daily_t)
                if vol_ok:
                    conf_ok = True
                    conf_msg = f"⚠️ FinMind 不可用 → 改用爆量：{vol_msg}"
                else:
                    conf_msg = f"{conf_msg} / {vol_msg}"
        else:
            conf_ok, conf_msg = self._check_volume_burst(df_daily_t)
        result.reasons.append(conf_msg)

        if conf_ok:
            result.label = "🔥 L2 (強勢)"
            result.score += 30
        else:
            result.label = "👀 L1 (觀察)"

        # v2.1：BEAR 強制 cap
        if regime == "BEAR" and "L2" in result.label:
            result.label = "👀 L1 (觀察) ⚠️ 熊市降級"
            result.reasons.append("⛔ 大盤跌破 200D-MA，L2 訊號自動降級")
            result.score = max(0, result.score - 20)

        return result

    def _get_market_regime(self, market: str,
                           target_date: Optional[date]) -> tuple[str, str]:
        df = self.engine.get_index_full(market)
        if df.empty:
            return "UNKNOWN", "ℹ️ 大盤資料缺失（中性處理）"
        df_t = self.engine.slice_until(df, target_date)
        if len(df_t) < REGIME_MA_DAYS:
            return "UNKNOWN", "ℹ️ 大盤歷史不足以判斷環境"
        ma = df_t["Close"].rolling(REGIME_MA_DAYS).mean().iloc[-1]
        cur = float(df_t["Close"].iloc[-1])
        idx_name = INDEX_MAP.get(market, "")
        if cur > ma * (1 + REGIME_BUFFER):
            return "BULL", f"🟢 {idx_name} 多頭 ({cur:.0f} > {REGIME_MA_DAYS}D-MA={ma:.0f})"
        if cur < ma * (1 - REGIME_BUFFER):
            return "BEAR", f"🔴 {idx_name} 空頭 ({cur:.0f} < {REGIME_MA_DAYS}D-MA={ma:.0f})"
        return "NEUTRAL", f"🟡 {idx_name} 盤整 ({cur:.0f} ≈ {REGIME_MA_DAYS}D-MA={ma:.0f})"

    def _calc_rs(self, df_stock: pd.DataFrame, market: str,
                 target_date: Optional[date]) -> tuple[Optional[float], str, int]:
        if len(df_stock) < RS_LOOKBACK_DAYS + 1:
            return None, "ℹ️ RS 樣本不足", 0
        df_idx = self.engine.get_index_full(market)
        if df_idx.empty:
            return None, "ℹ️ RS 無大盤對照", 0
        df_idx_t = self.engine.slice_until(df_idx, target_date)
        if len(df_idx_t) < RS_LOOKBACK_DAYS + 1:
            return None, "ℹ️ RS 大盤資料不足", 0

        s_now = float(df_stock["Close"].iloc[-1])
        s_then = float(df_stock["Close"].iloc[-RS_LOOKBACK_DAYS - 1])
        i_now = float(df_idx_t["Close"].iloc[-1])
        i_then = float(df_idx_t["Close"].iloc[-RS_LOOKBACK_DAYS - 1])
        if s_then == 0 or i_then == 0:
            return None, "ℹ️ RS 計算異常", 0

        s_ret = (s_now - s_then) / s_then * 100
        i_ret = (i_now - i_then) / i_then * 100
        rs = s_ret - i_ret

        if rs >= 10:
            return rs, f"🚀 RS={rs:+.1f}%（90 日大幅跑贏大盤）", 10
        if rs > 0:
            return rs, f"✅ RS={rs:+.1f}%（跑贏大盤）", 5
        if rs > -10:
            return rs, f"⚪ RS={rs:+.1f}%（與大盤同步）", 0
        return rs, f"⚠️ RS={rs:+.1f}%（明顯弱勢）", -10

    def _check_liquidity(self, df_daily: pd.DataFrame,
                         market: str) -> tuple[bool, str, float]:
        if len(df_daily) < 20:
            return False, "❌ 資料不足以計算流動性", 0.0
        recent = df_daily.tail(20)
        local_turnover = (recent["Close"] * recent["Volume"]).mean()
        fx = DataEngine.get_fx_to_twd(market)
        twd_turnover = local_turnover * fx
        if twd_turnover < MIN_DAILY_TURNOVER_TWD:
            return (False,
                    f"❌ 流動性不足（日均成交 ≈ {twd_turnover/1e8:.2f} 億 TWD < {MIN_DAILY_TURNOVER_TWD/1e8:.2f} 億）",
                    twd_turnover)
        return (True,
                f"✅ 流動性 OK（日均 ≈ {twd_turnover/1e8:.2f} 億 TWD）",
                twd_turnover)

    def _check_trend_weekly(self, df_w: pd.DataFrame,
                            current_price: float) -> tuple[bool, str, dict]:
        if len(df_w) < 32:
            return False, "❌ 週線資料不足以計算 30W-MA", {}
        ma30w = df_w["Close"].rolling(30).mean()
        ma_now = ma30w.iloc[-1]
        ma_4w_ago = ma30w.iloc[-5]
        slope_up = ma_now > ma_4w_ago
        above = current_price > ma_now
        extras = {"30W-MA": round(float(ma_now), 2)}
        if above and slope_up:
            return True, f"✅ 站上 30W-MA={ma_now:.2f} 且斜率向上", extras
        if not above:
            return False, f"❌ 跌破 30W-MA={ma_now:.2f}", extras
        return False, f"❌ 30W-MA={ma_now:.2f} 斜率向下", extras

    def _check_trend_daily(self, df_d: pd.DataFrame,
                           current_price: float) -> tuple[bool, str, dict]:
        if len(df_d) < 60:
            return False, "❌ 日線資料不足以計算 60D-MA", {}
        ma20 = df_d["Close"].rolling(20).mean().iloc[-1]
        ma60 = df_d["Close"].rolling(60).mean().iloc[-1]
        extras = {"20D-MA": round(float(ma20), 2), "60D-MA": round(float(ma60), 2)}
        if current_price > ma20 and current_price > ma60:
            return True, f"✅ 站穩 20D({ma20:.2f}) / 60D({ma60:.2f})", extras
        return False, f"❌ 未同時站穩 20D / 60D-MA", extras

    def _check_fundamentals(self, info: dict) -> tuple[str, int]:
        eps = info.get("trailingEps")
        rev_growth = info.get("revenueGrowth")
        if eps is None and rev_growth is None:
            return "ℹ️ 基本面資料缺失（中性）", 0
        if eps is not None and eps < 0 and (rev_growth is None or rev_growth < 0):
            return f"⚠️ EPS 為負且營收衰退（降級）", -15
        bits = []
        score = 0
        if eps is not None and eps > 0:
            bits.append(f"EPS={eps:.2f}>0")
            score += 5
        if rev_growth is not None and rev_growth > 0:
            bits.append(f"營收成長 {rev_growth*100:.1f}%")
            score += 5
        if not bits:
            return "ℹ️ 基本面數據存在但未過正向門檻", 0
        return "✅ 基本面 " + " / ".join(bits), score

    def _check_chips_tw(self, symbol: str, end_date: date) -> tuple[bool, str]:
        chips = DataEngine.get_tw_chips(symbol, end_date)
        if not chips["available"]:
            return False, f"⚪ 籌碼不可用：{chips['msg']}"
        net = chips["net_buy_lots"]
        pos = chips["positive_days"]
        if net >= FINMIND_NET_BUY_THRESHOLD and pos >= FINMIND_POSITIVE_DAYS_REQUIRED:
            return True, f"✅ 投信 5 日淨買超 {net} 張，正買天數 {pos}/{FINMIND_LOOKBACK_DAYS}"
        return False, f"⏳ 投信買盤未達標（淨買 {net} 張、正買 {pos}/{FINMIND_LOOKBACK_DAYS}）"

    def _check_volume_burst(self, df_daily: pd.DataFrame) -> tuple[bool, str]:
        # v2.4：看「過去 5 日內任一天爆量」，門檻 1.1x（比 1.2x 寬鬆）
        if len(df_daily) < 15:
            return False, "⚪ 量能資料不足"
        # 5~15 日前的均量當基準
        avg10 = df_daily["Volume"].iloc[-15:-5].mean()
        if avg10 == 0 or pd.isna(avg10):
            return False, "⚪ 無有效均量"
        recent_5 = df_daily["Volume"].iloc[-5:]
        max_ratio = float((recent_5 / avg10).max())
        if max_ratio >= 1.1:
            return True, f"✅ 近 5 日內爆量（峰值 / 10 期均量 = {max_ratio:.2f}x）"
        return False, f"⏳ 近 5 日量能未爆發（峰值 {max_ratio:.2f}x < 1.1x）"


# ============================================================
# 4. 模擬交易資料庫
# ============================================================

def db_conn() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db() -> None:
    with db_conn() as c:
        c.execute("""CREATE TABLE IF NOT EXISTS portfolio (
            symbol TEXT PRIMARY KEY, market TEXT NOT NULL,
            amount INTEGER NOT NULL, avg_cost REAL NOT NULL)""")
        c.execute("""CREATE TABLE IF NOT EXISTS account (
            id INTEGER PRIMARY KEY, cash_balance REAL NOT NULL)""")
        c.execute("""CREATE TABLE IF NOT EXISTS trades (
            id INTEGER PRIMARY KEY AUTOINCREMENT, ts TEXT NOT NULL,
            symbol TEXT NOT NULL, market TEXT NOT NULL, side TEXT NOT NULL,
            shares INTEGER NOT NULL, price REAL NOT NULL,
            fee REAL NOT NULL, tax REAL NOT NULL,
            cash_diff REAL NOT NULL, note TEXT)""")
        c.execute("INSERT OR IGNORE INTO account (id, cash_balance) VALUES (1, ?)",
                  (INITIAL_CASH,))
        c.commit()


def get_cash() -> float:
    with db_conn() as c:
        row = c.execute("SELECT cash_balance FROM account WHERE id=1").fetchone()
        return float(row["cash_balance"]) if row else 0.0


def set_cash(value: float) -> None:
    with db_conn() as c:
        c.execute("UPDATE account SET cash_balance=? WHERE id=1", (value,))
        c.commit()


def get_portfolio_df() -> pd.DataFrame:
    with db_conn() as c:
        rows = c.execute("SELECT * FROM portfolio ORDER BY symbol").fetchall()
    if not rows:
        return pd.DataFrame(columns=["symbol", "market", "amount", "avg_cost"])
    return pd.DataFrame([dict(r) for r in rows])


def get_trades_df(limit: int = 50) -> pd.DataFrame:
    with db_conn() as c:
        rows = c.execute("SELECT * FROM trades ORDER BY id DESC LIMIT ?",
                         (limit,)).fetchall()
    if not rows:
        return pd.DataFrame()
    return pd.DataFrame([dict(r) for r in rows])


def calc_costs(market: str, side: str, shares: int,
               price: float) -> tuple[float, float]:
    if market != "TW":
        return 0.0, 0.0
    gross = shares * price
    fee = max(20.0, gross * TW_FEE_RATE * TW_FEE_DISCOUNT)
    tax = gross * TW_TAX_RATE if side == "SELL" else 0.0
    return round(fee, 2), round(tax, 2)


def execute_trade(symbol: str, market: str, side: str, shares: int,
                  price: float, note: str = "") -> tuple[bool, str]:
    if shares <= 0 or price <= 0:
        return False, "股數與價格必須 > 0"
    fee, tax = calc_costs(market, side, shares, price)
    gross = shares * price
    with db_conn() as c:
        cash = float(c.execute("SELECT cash_balance FROM account WHERE id=1")
                     .fetchone()["cash_balance"])
        row = c.execute("SELECT * FROM portfolio WHERE symbol=?",
                        (symbol,)).fetchone()
        if side == "BUY":
            cost = gross + fee
            if cost > cash:
                return False, f"現金不足：需 {cost:,.0f}，餘 {cash:,.0f}"
            new_cash = cash - cost
            if row:
                old_amt, old_cost = row["amount"], row["avg_cost"]
                new_amt = old_amt + shares
                new_avg = (old_amt * old_cost + gross + fee) / new_amt
                c.execute("UPDATE portfolio SET amount=?, avg_cost=?, market=? WHERE symbol=?",
                          (new_amt, new_avg, market, symbol))
            else:
                avg = (gross + fee) / shares
                c.execute("INSERT INTO portfolio (symbol, market, amount, avg_cost) VALUES (?,?,?,?)",
                          (symbol, market, shares, avg))
            cash_diff = -cost
        elif side == "SELL":
            if not row or row["amount"] < shares:
                have = row["amount"] if row else 0
                return False, f"庫存不足：欲賣 {shares}，現有 {have}"
            proceeds = gross - fee - tax
            new_cash = cash + proceeds
            new_amt = row["amount"] - shares
            if new_amt == 0:
                c.execute("DELETE FROM portfolio WHERE symbol=?", (symbol,))
            else:
                c.execute("UPDATE portfolio SET amount=? WHERE symbol=?",
                          (new_amt, symbol))
            cash_diff = proceeds
        else:
            return False, f"未知 side: {side}"
        c.execute("UPDATE account SET cash_balance=? WHERE id=1", (new_cash,))
        c.execute("""INSERT INTO trades
                     (ts, symbol, market, side, shares, price, fee, tax, cash_diff, note)
                     VALUES (?,?,?,?,?,?,?,?,?,?)""",
                  (datetime.now().isoformat(timespec="seconds"), symbol, market,
                   side, shares, price, fee, tax, cash_diff, note))
        c.commit()
    return True, f"{side} {symbol} x {shares} @ {price}（fee={fee}, tax={tax}）"


# ============================================================
# 5. UI 工具
# ============================================================

def fmt_money(v: float) -> str:
    return f"{v:,.0f}"


def init_session() -> None:
    if "scan_result" not in st.session_state:
        st.session_state["scan_result"] = None
    if "scan_meta" not in st.session_state:
        st.session_state["scan_meta"] = {}
    if "risk_result" not in st.session_state:
        st.session_state["risk_result"] = None


# ============================================================
# 6. 三大 Tab
# ============================================================

def render_radar_tab():
    st.subheader("🛰️ 戰略雷達 — 跨國掃描 + 時光機")

    col1, col2, col3 = st.columns([2, 1, 1])
    with col1:
        pool_name = st.selectbox("選擇股池", SourceProvider.list_pools(), index=0)
    with col2:
        strategy = st.radio("策略週期", ["Weekly", "Daily"],
                            index=0, horizontal=True,
                            help="Weekly = 經典波段（30W-MA）；Daily = 靈活操作（20D / 60D-MA）")
    with col3:
        use_tm = st.toggle("⏰ 時光機", value=False,
                           help="開啟後可指定一個歷史日期，模擬當天做決策的結果")

    target_date: Optional[date] = None
    backtest_horizon = 0
    if use_tm:
        d_col1, d_col2 = st.columns(2)
        with d_col1:
            target_date = st.date_input(
                "判斷日期 (target_date)",
                value=date.today() - timedelta(days=180),
                min_value=date.today() - timedelta(days=365 * 2),
                max_value=date.today() - timedelta(days=30),
            )
        with d_col2:
            backtest_horizon = st.selectbox(
                "回看天數（驗證該日決策半年/一年後表現）",
                [60, 120, 180, 365], index=2,
            )

    pool = SourceProvider.get(pool_name)
    market = pool["market"]
    symbols = pool["symbols"]
    st.caption(f"市場：**{market}** ／ 股池：{', '.join(symbols)}")

    run = st.button("🚀 開始掃描", type="primary", use_container_width=True)

    if run:
        with st.spinner(f"掃描中…（{len(symbols)} 檔，可能需要 30~60 秒）"):
            v = GlobalValidator()
            rows = []
            backtest_rows = []
            prog = st.progress(0.0)
            for i, sym in enumerate(symbols, start=1):
                res = v.validate(sym, market, strategy, target_date)
                rows.append(res)

                # v2.1 時光機回測：買進價改用 target_date 隔日開盤價
                if use_tm and target_date and res.current_price:
                    try:
                        df_full, _, _ = DataEngine.get_daily(sym, market)
                        if not df_full.empty:
                            future_end = target_date + timedelta(days=backtest_horizon)
                            df_future = df_full[
                                (df_full.index > pd.Timestamp(target_date))
                                & (df_full.index <= pd.Timestamp(future_end))
                            ]
                            if not df_future.empty:
                                entry_price = float(df_future["Open"].iloc[0])
                                exit_price = float(df_future["Close"].iloc[-1])
                                ret_pct = (exit_price - entry_price) / entry_price * 100
                                close_series = df_future["Close"]
                                running_max = close_series.cummax()
                                drawdowns = (close_series - running_max) / running_max * 100
                                true_mdd = float(drawdowns.min())
                                max_price = float(df_future["High"].max())
                                max_run = (max_price - entry_price) / entry_price * 100
                                backtest_rows.append({
                                    "代號": res.symbol,
                                    "評級": res.label,
                                    "判斷日收盤": round(res.current_price, 2),
                                    "隔日開盤(實際買價)": round(entry_price, 2),
                                    f"{backtest_horizon}日後收盤": round(exit_price, 2),
                                    "報酬%": round(ret_pct, 2),
                                    "期間最大漲幅%": round(max_run, 2),
                                    "真實MDD%": round(true_mdd, 2),
                                })
                    except Exception:
                        pass

                prog.progress(i / len(symbols))
            prog.empty()

            df_main = pd.DataFrame([r.to_dict() for r in rows])
            st.session_state["scan_result"] = df_main
            st.session_state["scan_meta"] = {
                "pool": pool_name, "market": market, "strategy": strategy,
                "target_date": target_date.isoformat() if target_date else None,
                "scanned_at": datetime.now().isoformat(timespec="seconds"),
                "backtest": backtest_rows,
                "regime": rows[0].extras.get("市場環境", "UNKNOWN") if rows else "UNKNOWN",
            }

    df_result = st.session_state.get("scan_result")
    meta = st.session_state.get("scan_meta", {})
    if df_result is not None and not df_result.empty:
        st.markdown("---")

        # v2.1：市場溫度計
        regime = meta.get("regime", "UNKNOWN")
        regime_box = {
            "BULL":    ("🟢 多頭環境", "正常選股，L2 有效", "success"),
            "NEUTRAL": ("🟡 盤整環境", "謹慎選股，建議減碼", "warning"),
            "BEAR":    ("🔴 空頭環境", "L2 已自動降級為觀察，建議空手", "error"),
            "UNKNOWN": ("⚪ 大盤資料缺失", "無法判斷大盤環境", "info"),
        }.get(regime, ("⚪", "", "info"))
        getattr(st, regime_box[2])(f"**市場溫度計：{regime_box[0]}** — {regime_box[1]}")

        st.write(f"📊 **掃描時間：** {meta.get('scanned_at','-')} ／ "
                 f"**策略：** {meta.get('strategy','-')} ／ "
                 f"**判斷日：** {meta.get('target_date') or '今日'}")

        l2 = df_result[df_result["評級"].astype(str).str.contains("L2")]
        if not l2.empty:
            st.success(f"🔥 強勢股 ({len(l2)} 檔)")
            st.dataframe(l2, use_container_width=True, hide_index=True)

        l1 = df_result[df_result["評級"].astype(str).str.contains("L1")]
        if not l1.empty:
            with st.expander(f"👀 觀察名單 ({len(l1)} 檔)", expanded=False):
                st.dataframe(l1, use_container_width=True, hide_index=True)

        with st.expander("📋 完整報表（含淘汰 / 風險過高）", expanded=False):
            st.dataframe(df_result, use_container_width=True, hide_index=True)

        backtest = meta.get("backtest", [])
        if backtest:
            st.markdown("### ⏰ 時光機回測結果（法人視角）")
            st.caption("買進價 = target_date 隔日開盤價（避免未來函數）；MDD 為期間真實最大回撤")
            df_bt = pd.DataFrame(backtest)
            l2_only = df_bt[df_bt["評級"].astype(str).str.contains("L2")]

            c1, c2, c3, c4 = st.columns(4)
            c1.metric("全樣本平均報酬", f"{df_bt['報酬%'].mean():.2f}%")
            c2.metric("全樣本勝率", f"{(df_bt['報酬%'] > 0).mean() * 100:.1f}%")
            if not l2_only.empty:
                c3.metric("L2 平均報酬", f"{l2_only['報酬%'].mean():.2f}%",
                          delta=f"{l2_only['報酬%'].mean() - df_bt['報酬%'].mean():.2f}% vs 全樣本")
                c4.metric("L2 勝率", f"{(l2_only['報酬%'] > 0).mean() * 100:.1f}%")
            else:
                c3.metric("L2 平均報酬", "—")
                c4.metric("L2 勝率", "—")

            with st.expander("📐 進階風險指標（CAGR / 平均 MDD / 賠率）"):
                horizon_days = next((int(c.replace("日後收盤", ""))
                                     for c in df_bt.columns if "日後收盤" in c), 180)
                cagr_full = (((1 + df_bt["報酬%"].mean() / 100)
                              ** (252 / horizon_days)) - 1) * 100
                avg_mdd = df_bt["真實MDD%"].mean()
                wins = df_bt[df_bt["報酬%"] > 0]["報酬%"].mean()
                losses = df_bt[df_bt["報酬%"] < 0]["報酬%"].mean()
                payoff = abs(wins / losses) if losses and not pd.isna(losses) else float("nan")
                cc1, cc2, cc3 = st.columns(3)
                cc1.metric("年化報酬率 CAGR", f"{cagr_full:.2f}%")
                cc2.metric("平均 MDD", f"{avg_mdd:.2f}%")
                cc3.metric("賠率（平均賺/平均賠）",
                           f"{payoff:.2f}" if payoff == payoff else "—")

            st.dataframe(df_bt, use_container_width=True, hide_index=True)
    else:
        st.info("尚未掃描。選擇股池與策略後按『開始掃描』。")


def render_lab_tab():
    st.subheader("🧪 模擬實驗室 — 紙上下單")

    cash = get_cash()
    portfolio = get_portfolio_df()
    st.metric("💰 模擬現金 (TWD)", fmt_money(cash))

    with st.form("trade_form", clear_on_submit=True):
        c1, c2, c3, c4, c5 = st.columns([1, 1, 1, 1, 1])
        with c1:
            symbol = st.text_input("代號",
                                   placeholder="2330 / NVDA / 8001.T").strip()
        with c2:
            market = st.selectbox("市場", ["TW", "US", "JP"])
        with c3:
            side = st.radio("方向", ["BUY", "SELL"], horizontal=True)
        with c4:
            shares = st.number_input("股數", min_value=1,
                                     value=1000 if market == "TW" else 10, step=1)
        with c5:
            price = st.number_input("價格", min_value=0.01,
                                    value=100.0, step=0.01, format="%.2f")
        note = st.text_input("備註（可選）", placeholder="例如：L2 強勢進場")
        submitted = st.form_submit_button("✅ 送出模擬單", type="primary")

    if submitted:
        ok, msg = execute_trade(symbol.upper() if market != "TW" else symbol,
                                market, side, int(shares), float(price), note)
        if ok:
            st.success(msg)
            st.rerun()
        else:
            st.error(msg)

    st.markdown("---")
    st.markdown("### 📦 目前持股")
    if portfolio.empty:
        st.info("目前沒有持股。")
    else:
        st.dataframe(portfolio, use_container_width=True, hide_index=True)

    st.markdown("### 📜 最近 50 筆交易紀錄")
    trades = get_trades_df(50)
    if trades.empty:
        st.info("尚無交易紀錄。")
    else:
        st.dataframe(trades, use_container_width=True, hide_index=True)

    with st.expander("⚙️ 進階：重置模擬帳戶"):
        if st.button("🗑️ 清空持股、交易紀錄並重置現金", type="secondary"):
            with db_conn() as c:
                c.execute("DELETE FROM portfolio")
                c.execute("DELETE FROM trades")
                c.execute("UPDATE account SET cash_balance=? WHERE id=1",
                          (INITIAL_CASH,))
                c.commit()
            st.success("已重置。")
            st.rerun()


def render_risk_tab():
    st.subheader("🛡️ 風控儀表板 — 庫存即時體檢")

    portfolio = get_portfolio_df()
    if portfolio.empty:
        st.info("目前沒有庫存可體檢。先到『模擬實驗室』下單吧。")
        return

    strategy = st.radio("使用哪一種策略檢查庫存？",
                        ["Weekly", "Daily"], horizontal=True, index=0)

    if st.button("🔄 重新體檢全部庫存", type="primary"):
        v = GlobalValidator()
        rows = []
        prog = st.progress(0.0)
        for i, (_, p) in enumerate(portfolio.iterrows(), start=1):
            res = v.validate(p["symbol"], p["market"], strategy, target_date=None)
            cur = res.current_price or 0.0
            cost = float(p["avg_cost"])
            qty = int(p["amount"])
            pnl_pct = (cur - cost) / cost * 100 if cost else 0.0
            rows.append({
                "代號": res.symbol,
                "市場": p["market"],
                "庫存": qty,
                "成本": round(cost, 2),
                "現價": round(cur, 2),
                "未實現損益%": round(pnl_pct, 2),
                "現況評級": res.label,
                "市場環境": res.extras.get("市場環境", "—"),
                "RS_90D_%": res.extras.get("RS_90D_%", "—"),
                "理由": " / ".join(res.reasons),
            })
            prog.progress(i / len(portfolio))
        prog.empty()
        st.session_state["risk_result"] = pd.DataFrame(rows)

    df_risk = st.session_state.get("risk_result")
    if df_risk is None or df_risk.empty:
        st.info("尚未體檢，按上方按鈕開始。")
        return

    danger = df_risk[df_risk["現況評級"].astype(str).str.contains("L0|⛔")]
    if not danger.empty:
        st.error(f"🚨 共 {len(danger)} 檔已跌破生命線或流動性異常！")
        st.dataframe(danger, use_container_width=True, hide_index=True)

    watch = df_risk[df_risk["現況評級"].astype(str).str.contains("L1")]
    if not watch.empty:
        st.warning(f"⚠️ {len(watch)} 檔降級為 L1 觀察")
        st.dataframe(watch, use_container_width=True, hide_index=True)

    healthy = df_risk[df_risk["現況評級"].astype(str).str.contains("L2")]
    if not healthy.empty:
        st.success(f"✅ {len(healthy)} 檔仍維持 L2 強勢")
        st.dataframe(healthy, use_container_width=True, hide_index=True)


# ============================================================
# 7. 主入口
# ============================================================

def main():
    init_db()
    init_session()

    st.title("📈 Alpha-X Global v2.1")
    st.caption("跨市場（台 / 美 / 日）波段決策 ｜ 法人級篩選 ｜ 時光機回測 ｜ 模擬交易與風控")

    with st.sidebar:
        st.markdown("### ℹ️ 系統狀態")
        st.write(f"FinMind 套件：{'✅ 已安裝' if FINMIND_AVAILABLE else '❌ 未安裝'}")
        st.caption(f"資料庫：`{DB_PATH}`")
        st.markdown("---")
        st.markdown("### 🧭 策略總覽 (v2.1)")
        st.markdown(
            "**🌐 大盤環境** (NEW)\n"
            "- BULL → 正常 L2\n"
            "- NEUTRAL → 提醒\n"
            "- BEAR → L2 自動降級為觀察\n\n"
            "**🚦 個股漏斗**\n"
            "- L0 流動性：日均成交 ≥ 5,000 萬\n"
            "- L1 趨勢：站上 30W-MA 或 20D/60D\n"
            "- L1 基本面：EPS / 營收成長（軟性）\n"
            "- L1.5 RS (NEW)：90 日跑贏大盤\n"
            "- L2 確認：台股投信、美/日爆量\n\n"
            "**⏰ 時光機**\n"
            "- 買進價 = 隔日開盤（無未來函數）\n"
            "- 模式下跳過基本面評分"
        )

    tab1, tab2, tab3 = st.tabs(["🛰️ 戰略雷達", "🧪 模擬實驗室", "🛡️ 風控儀表板"])
    with tab1:
        render_radar_tab()
    with tab2:
        render_lab_tab()
    with tab3:
        render_risk_tab()


if __name__ == "__main__":
    main()

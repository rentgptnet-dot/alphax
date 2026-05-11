"""
backtest.py — Alpha-X 歷史回測腳本
====================================

避免回測幻覺的 8 條原則（必讀）：
  1. 包含熊市：必含 2022 全年 FED 升息熊市
  2. 全部訊號計入：所有 L2 都記錄，不能事後 cherry-pick
  3. 等權買入：每檔分配相同金額，不主觀加碼
  4. 固定持有期：60 日固定，不停損不追蹤停利（避免額外自由度）
  5. 扣交易成本：滑價 0.3% + 手續費 0.1425% + 賣稅 0.3%
  6. 對照 baseline：所有報酬都 vs 0050 同期被動持有
  7. 隔日開盤買：避免「當日收盤訊號當日成交」的未來函數
  8. 透明攤開：每月詳細紀錄，不藏失敗案例

Usage:
    python3 backtest.py                  # 預設 24 個月，60 日 horizon
    python3 backtest.py --months 36      # 自訂回測月數
    python3 backtest.py --horizon 90     # 自訂持有天數
    python3 backtest.py --out custom.json
"""

from __future__ import annotations

import argparse
import json
from datetime import date, timedelta
from typing import Optional

import pandas as pd

from app import GlobalValidator, DataEngine
from pools import build_dashboard_universe


# ============================================================
# 交易成本設定（誠實、悲觀）
# ============================================================
SLIPPAGE_PCT = 0.003       # 滑價 0.3%（台股中型股的真實估計）
TW_FEE_RATE = 0.001425     # 手續費 0.1425%
TW_TAX_RATE = 0.003        # 證交稅 0.3%（只賣出收）


def first_trading_day_each_month(start: date, end: date) -> list[date]:
    """每月第一個交易日（business day）。"""
    months = pd.date_range(start, end, freq="MS")
    out = []
    for m in months:
        bdays = pd.bdate_range(m, m + timedelta(days=14))
        if len(bdays) > 0:
            out.append(bdays[0].date())
    return out


def net_return_pct(entry_price: float, exit_price: float) -> float:
    """扣除全部交易成本後的淨報酬率（%）。"""
    cost_to_buy = entry_price * (1 + SLIPPAGE_PCT + TW_FEE_RATE)
    proceeds = exit_price * (1 - SLIPPAGE_PCT - TW_FEE_RATE - TW_TAX_RATE)
    return (proceeds - cost_to_buy) / cost_to_buy * 100


def backtest_single(
    symbol: str, market: str, target_date: date, horizon: int
) -> Optional[dict]:
    """對單一 L2 訊號，計算 target_date 隔日進場 → horizon 日後出場的結果。"""
    df_full, _, _ = DataEngine.get_daily(symbol, market)
    if df_full.empty:
        return None
    after = df_full[df_full.index > pd.Timestamp(target_date)]
    if len(after) < 10:
        return None  # 資料不足
    entry_price = float(after["Open"].iloc[0])
    entry_date = after.index[0].date()
    end_idx = min(horizon, len(after)) - 1
    exit_price = float(after["Close"].iloc[end_idx])
    exit_date = after.index[end_idx].date()
    # 期間最大回撤
    period = after.iloc[: end_idx + 1]["Close"]
    running_max = period.cummax()
    dd = ((period - running_max) / running_max).min() * 100
    return {
        "entry_price": round(entry_price, 2),
        "entry_date": entry_date.isoformat(),
        "exit_price": round(exit_price, 2),
        "exit_date": exit_date.isoformat(),
        "net_return_pct": round(net_return_pct(entry_price, exit_price), 2),
        "max_drawdown_pct": round(float(dd), 2),
    }


def backtest_baseline(target_date: date, horizon: int) -> Optional[float]:
    """0050 同期持有的淨報酬。"""
    r = backtest_single("0050", "TW", target_date, horizon)
    return r["net_return_pct"] if r else None


def run_backtest(months: int, horizon: int) -> dict:
    universe = build_dashboard_universe()
    validator = GlobalValidator()

    end = date.today()
    # 留 horizon * 1.5 天讓最後一個月有完整持有期
    last_target = end - timedelta(days=int(horizon * 1.5))
    start = end - timedelta(days=months * 31)
    target_dates = first_trading_day_each_month(start, last_target)

    print(f"📊 Backtest universe: {len(universe)} symbols")
    print(f"📅 Target dates: {len(target_dates)} months "
          f"({target_dates[0]} → {target_dates[-1]})")
    print(f"⏱  Horizon: {horizon} trading days")
    print(f"💸 Costs: slippage {SLIPPAGE_PCT*100}% + fee {TW_FEE_RATE*100:.4f}% "
          f"+ tax {TW_TAX_RATE*100}%\n")

    by_month: list[dict] = []
    all_returns: list[float] = []

    for td in target_dates:
        print(f"[{td}] scanning {len(universe)} symbols...", end=" ", flush=True)
        l2_picks: list[dict] = []
        for sym in universe:
            res = validator.validate(sym, "TW", "Weekly", target_date=td)
            if "L2" in res.label:
                pick = backtest_single(sym, "TW", td, horizon)
                if pick is None:
                    continue
                pick["symbol"] = res.symbol
                pick["rs_90d"] = res.extras.get("RS_90D_%")
                pick["regime"] = res.extras.get("市場環境")
                l2_picks.append(pick)
                all_returns.append(pick["net_return_pct"])

        baseline = backtest_baseline(td, horizon)
        avg = (sum(p["net_return_pct"] for p in l2_picks) / len(l2_picks)
               if l2_picks else 0.0)
        win_rate = (sum(1 for p in l2_picks if p["net_return_pct"] > 0)
                    / len(l2_picks) * 100 if l2_picks else 0.0)

        by_month.append({
            "month": td.strftime("%Y-%m"),
            "target_date": td.isoformat(),
            "signal_count": len(l2_picks),
            "avg_return_pct": round(avg, 2),
            "win_rate_pct": round(win_rate, 1),
            "baseline_return_pct": (round(baseline, 2)
                                     if baseline is not None else None),
            "alpha_pct": (round(avg - baseline, 2)
                          if baseline is not None else None),
            "picks": l2_picks,
        })
        print(f"L2={len(l2_picks)}  avg={avg:+.2f}%  baseline={baseline}")

    # ============================================================
    # 統計總結
    # ============================================================
    total = len(all_returns)
    wins = [r for r in all_returns if r > 0]
    losses = [r for r in all_returns if r < 0]
    win_rate = len(wins) / total * 100 if total else 0
    avg_return = sum(all_returns) / total if total else 0
    avg_win = sum(wins) / len(wins) if wins else 0
    avg_loss = sum(losses) / len(losses) if losses else 0
    payoff = abs(avg_win / avg_loss) if avg_loss else float("nan")
    max_loss = min(all_returns) if all_returns else 0
    max_gain = max(all_returns) if all_returns else 0

    baseline_rets = [m["baseline_return_pct"] for m in by_month
                     if m["baseline_return_pct"] is not None]
    avg_baseline = sum(baseline_rets) / len(baseline_rets) if baseline_rets else 0

    # 月度連虧
    monthly_avgs = [m["avg_return_pct"] for m in by_month if m["signal_count"] > 0]
    max_consec_loss = cur = 0
    for r in monthly_avgs:
        if r < 0:
            cur += 1
            max_consec_loss = max(max_consec_loss, cur)
        else:
            cur = 0

    cagr = ((1 + avg_return / 100) ** (252 / horizon) - 1) * 100 if avg_return else 0

    return {
        "generated_at": pd.Timestamp.now().isoformat(timespec="seconds"),
        "horizon_days": horizon,
        "method": {
            "stock_pool_size": len(universe),
            "stock_pool_desc": "台股市值前 100 + AI 伺服器 + 高股息 ETF + 電動車供應鏈",
            "frequency": "每月第一個交易日掃描",
            "horizon": f"{horizon} 個交易日固定持有（不停損不停利）",
            "cost_assumption": (f"滑價 {SLIPPAGE_PCT*100:.1f}% + "
                                f"手續費 {TW_FEE_RATE*100:.4f}% + "
                                f"賣稅 {TW_TAX_RATE*100:.1f}%"),
            "baseline": "0050 同期等權持有",
            "lookahead_protection": "時光機切片 + 隔日開盤買進 + 跳過基本面",
        },
        "summary": {
            "months": len(by_month),
            "total_signals": total,
            "win_rate_pct": round(win_rate, 1),
            "avg_return_pct": round(avg_return, 2),
            "baseline_avg_return_pct": round(avg_baseline, 2),
            "alpha_pct": round(avg_return - avg_baseline, 2),
            "avg_win_pct": round(avg_win, 2),
            "avg_loss_pct": round(avg_loss, 2),
            "payoff_ratio": round(payoff, 2) if payoff == payoff else None,
            "max_single_gain_pct": round(max_gain, 2),
            "max_single_loss_pct": round(max_loss, 2),
            "max_consecutive_losing_months": max_consec_loss,
            "cagr_pct": round(cagr, 2),
        },
        "by_month": by_month,
    }


def main():
    parser = argparse.ArgumentParser(description="Alpha-X 歷史回測")
    parser.add_argument("--months", type=int, default=24,
                        help="回測月數（預設 24）")
    parser.add_argument("--horizon", type=int, default=60,
                        help="持有天數（交易日，預設 60）")
    parser.add_argument("--out", default="backtest_results.json",
                        help="輸出檔名")
    args = parser.parse_args()

    result = run_backtest(args.months, args.horizon)

    with open(args.out, "w", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False, indent=2)

    s = result["summary"]
    print("\n" + "=" * 60)
    print("✅ Backtest 完成！")
    print("=" * 60)
    print(f"  總訊號數:           {s['total_signals']}")
    print(f"  勝率:               {s['win_rate_pct']}%")
    print(f"  平均報酬:           {s['avg_return_pct']:+.2f}%")
    print(f"  0050 baseline:     {s['baseline_avg_return_pct']:+.2f}%")
    print(f"  Alpha:              {s['alpha_pct']:+.2f}%")
    print(f"  賠率:               {s['payoff_ratio']}")
    print(f"  單筆最大虧損:       {s['max_single_loss_pct']:.2f}%")
    print(f"  最大連虧月數:       {s['max_consecutive_losing_months']}")
    print(f"  CAGR (年化):        {s['cagr_pct']:+.2f}%")
    print(f"\n結果已存到 {args.out}")


if __name__ == "__main__":
    main()

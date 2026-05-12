"""
Alpha-X 公開看板 (dashboard.py)
================================
給朋友看的純看板版本。一頁式、唯讀、不需要登入。

★ v2.3 公開模式 (PUBLIC_MODE)：
  - 預設 = 自用模式（顯示完整代號 / 公司名 / "L2 強勢"）
  - 設環境變數 ALPHAX_PUBLIC=1 或 Streamlit Cloud secrets 加入 public_mode = true
    → 切換成公開模式：個股匿名化（半導體 #1、電子代工 #2…），中性措辭

設計原則：
  - 唯讀：沒有交易按鈕
  - 預跑好：背後讀 backtest_results.json，看板讀 JSON 顯示
  - 行動友善：響應式
  - 法律安全：清楚免責 + 純娛樂定位 + 公開模式可匿名

Usage:
    streamlit run dashboard.py                    # 自用模式
    ALPHAX_PUBLIC=1 streamlit run dashboard.py    # 公開模式（本機）
    # Streamlit Cloud：在 secrets 加 public_mode = true
"""

from __future__ import annotations

import json
import os
from datetime import date

import pandas as pd
import streamlit as st

from app import GlobalValidator, DataEngine, FINMIND_AVAILABLE
from pools import (
    DASHBOARD_GROUPS, build_dashboard_universe,
    get_sector, anonymize_picks,
)


# ============================================================
# 設定
# ============================================================

st.set_page_config(
    page_title="Alpha-X 台股看板",
    layout="wide",
    page_icon="📈",
    menu_items={"About": "Alpha-X — 台股看板（僅供娛樂、非投資建議）"},
)

BACKTEST_FILE = "backtest_results.json"
MOCK_BACKTEST_FILE = "mock_backtest_results.json"


# ★ v2.3：公開模式偵測
def _detect_public_mode() -> bool:
    """優先讀 st.secrets，其次讀環境變數，預設 False。
    只有 secrets 明確設為 truthy 才用 secrets；否則 fall through 到 env var。"""
    try:
        if hasattr(st, "secrets"):
            val = st.secrets.get("public_mode", None)
            if val is not None and bool(val):
                return True
    except Exception:
        pass
    return os.getenv("ALPHAX_PUBLIC", "0") == "1"

PUBLIC_MODE = _detect_public_mode()


# ============================================================
# 中性措辭（公開模式用）
# ============================================================

WORDING = {
    True: {  # PUBLIC mode
        "title": "📊 Alpha-X 台股篩選看板",
        "subtitle": "依規則自動篩選 · 過去資料回看 · 個股匿名 · 僅供娛樂研究",
        "today_section_title": "📋 今日通過全部篩選條件的個股",
        "today_caption": "（個股已匿名為產業代號；想看完整資訊請聯絡作者本人）",
        "no_picks": "今天沒有個股通過全部篩選條件 — 可能是大盤環境不佳或規則未滿足。",
        "history_title": "📅 每月篩選結果回看",
        "history_caption": "全部攤開，無篩選 — 賠錢的月份照樣顯示",
        "regime_bull": ("🟢 大盤多頭 (BULL)", "規則正常套用"),
        "regime_neutral": ("🟡 大盤盤整 (NEUTRAL)", "規則套用結果建議謹慎參考"),
        "regime_bear": ("🔴 大盤空頭 (BEAR)", "通過條件已自動降級"),
        "regime_unknown": ("⚪ 大盤資料缺失", ""),
        "method_disclaimer": "※ 本看板僅展示「依固定規則對公開資料的篩選結果與歷史回看」，"
                              "不構成任何個股的買賣建議或預測。",
    },
    False: {  # PRIVATE / 自用 mode
        "title": "📈 Alpha-X 台股強勢看板",
        "subtitle": "每日收盤後自動更新 · 涵蓋台股市值前 100 名 + 主題股 · 自用版",
        "today_section_title": "🔥 今日 L2 強勢股",
        "today_caption": "通過全部過濾條件的個股",
        "no_picks": "今天沒有符合 L2 條件的個股。",
        "history_title": "📅 每月詳細紀錄",
        "history_caption": "全部攤開，沒有 cherry-pick — 賠錢月份照樣顯示",
        "regime_bull": ("🟢 多頭環境 (BULL)", "正常選股，L2 訊號有效"),
        "regime_neutral": ("🟡 盤整環境 (NEUTRAL)", "謹慎選股，建議減碼"),
        "regime_bear": ("🔴 空頭環境 (BEAR)", "L2 已自動降為觀察，建議空手"),
        "regime_unknown": ("⚪ 大盤資料缺失", "無法判斷市場環境"),
        "method_disclaimer": "",
    },
}

W = WORDING[PUBLIC_MODE]


# ============================================================
# 資料載入
# ============================================================

@st.cache_data(ttl=60 * 60, show_spinner=False)
def load_backtest() -> dict | None:
    for path in (BACKTEST_FILE, MOCK_BACKTEST_FILE):
        if os.path.exists(path):
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
            data["_source"] = path
            return data
    return None


@st.cache_data(ttl=60 * 30, show_spinner=False)
def scan_today() -> tuple[str, str, list[dict], list[dict]]:
    """回傳 (regime, regime_msg, L2_picks, L1_picks)。"""
    validator = GlobalValidator()
    universe = build_dashboard_universe()
    l2_picks: list[dict] = []
    l1_picks: list[dict] = []
    regime, regime_msg = "UNKNOWN", "—"

    for sym in universe:
        try:
            res = validator.validate(sym, "TW", "Weekly")
        except Exception:
            continue
        regime = res.extras.get("市場環境", regime)
        if regime_msg == "—" and res.reasons:
            for r in res.reasons:
                if "TWII" in r or "200" in r:
                    regime_msg = r
                    break
        item = {
            "symbol": res.symbol,
            "label": res.label,
            "price": res.current_price,
            "rs_90d": res.extras.get("RS_90D_%"),
            "reasons": res.reasons,
        }
        if "L2" in res.label:
            l2_picks.append(item)
        elif "L1" in res.label:
            l1_picks.append(item)
    return regime, regime_msg, l2_picks, l1_picks


# ============================================================
# UI 元件
# ============================================================

def render_header():
    st.markdown(f"<h1 style='margin-bottom:0;'>{W['title']}</h1>",
                unsafe_allow_html=True)
    st.caption(W["subtitle"])
    if PUBLIC_MODE:
        st.info("ℹ️ **本頁為公開展示版本**。所有個股已匿名化為產業代號（如「半導體 #1」），"
                "完整資訊僅作者本人持有。")


def render_regime_banner(regime: str, msg: str):
    box_map = {
        "BULL":    (W["regime_bull"], "success"),
        "NEUTRAL": (W["regime_neutral"], "warning"),
        "BEAR":    (W["regime_bear"], "error"),
        "UNKNOWN": (W["regime_unknown"], "info"),
    }
    (title, subtitle), kind = box_map.get(regime, box_map["UNKNOWN"])
    body = f"**市場溫度計：{title}**\n\n{subtitle}"
    if msg and msg != "—":
        body += f" · {msg}"
    getattr(st, kind)(body)


def _make_today_table_row(p: dict, idx: int, sector_counter: dict) -> dict:
    """單一 pick 轉成顯示用 row。會依 PUBLIC_MODE 決定要不要匿名。"""
    if PUBLIC_MODE:
        sym = p.get("symbol", "")
        sector = get_sector(sym)
        sector_counter[sector] = sector_counter.get(sector, 0) + 1
        display = f"{sector} #{sector_counter[sector]}"
        return {
            "篩選結果": display,
            "RS 90D": (f"{p['rs_90d']:+.1f}%"
                      if p.get("rs_90d") is not None else "—"),
        }
    else:
        chips_msg = ""
        for r in p.get("reasons", []):
            if "投信" in r or "爆量" in r:
                chips_msg = r
                break
        return {
            "代號": p.get("symbol", ""),
            "收盤價": (round(p["price"], 2) if p.get("price") else None),
            "RS 90D": (f"{p['rs_90d']:+.1f}%"
                      if p.get("rs_90d") is not None else "—"),
            "確認訊號": chips_msg,
        }


def render_today_section(picks: list[dict]):
    today_str = date.today().isoformat()
    st.markdown(f"### {W['today_section_title']}（{today_str}）")
    if not picks:
        st.info(W["no_picks"])
        return
    sector_counter: dict = {}
    rows = [_make_today_table_row(p, i, sector_counter)
            for i, p in enumerate(picks)]
    st.dataframe(pd.DataFrame(rows), use_container_width=True, hide_index=True)
    st.caption(f"{W['today_caption']} · 共 {len(picks)} 檔 / 全池 ~100 檔")


def render_today_section_from_mock(today_picks: list[dict]):
    st.markdown(f"### {W['today_section_title']}（範例 / 待你實際跑掃描）")
    sector_counter: dict = {}
    rows = []
    for p in today_picks:
        if PUBLIC_MODE:
            sym = p.get("symbol", "")
            sector = get_sector(sym)
            sector_counter[sector] = sector_counter.get(sector, 0) + 1
            rows.append({
                "篩選結果": f"{sector} #{sector_counter[sector]}",
                "RS 90D": (f"{p['rs_90d']:+.1f}%"
                          if p.get("rs_90d") is not None else "—"),
            })
        else:
            rows.append({
                "代號": p.get("symbol", ""),
                "公司": p.get("name", "—"),
                "收盤價": p.get("price"),
                "RS 90D": (f"{p['rs_90d']:+.1f}%"
                          if p.get("rs_90d") is not None else "—"),
                "確認訊號": p.get("chips_msg", ""),
            })
    st.dataframe(pd.DataFrame(rows), use_container_width=True, hide_index=True)
    st.caption(W["today_caption"])


def render_summary_metrics(summary: dict, horizon: int):
    st.markdown(f"### 📊 過去 {summary['months']} 個月回看績效"
                f"（{horizon} 日持有）")
    c1, c2, c3, c4 = st.columns(4)
    avg, base = summary["avg_return_pct"], summary["baseline_avg_return_pct"]
    alpha, win = summary["alpha_pct"], summary["win_rate_pct"]
    c1.metric("規則平均報酬", f"{avg:+.2f}%",
              help=f"{summary['total_signals']} 個訊號的平均淨報酬（已扣交易成本）")
    c2.metric("vs 0050 同期", f"{base:+.2f}%",
              help="同期間直接買 0050 ETF 的對照")
    c3.metric("超額報酬 (Alpha)", f"{alpha:+.2f}%",
              delta=f"{'跑贏' if alpha > 0 else '跑輸'} 大盤")
    c4.metric("勝率", f"{win:.1f}%",
              help=f"{summary['total_signals']} 筆訊號中報酬為正的比率")

    with st.expander("📐 進階風險指標"):
        d1, d2, d3, d4 = st.columns(4)
        d1.metric("CAGR (年化)", f"{summary['cagr_pct']:+.2f}%")
        d2.metric("賠率",
                  f"{summary['payoff_ratio']:.2f}"
                  if summary['payoff_ratio'] else "—")
        d3.metric("單筆最大虧損", f"{summary['max_single_loss_pct']:.2f}%")
        d4.metric("最大連虧月數",
                  f"{summary['max_consecutive_losing_months']} 個月")
        e1, e2 = st.columns(2)
        e1.metric("平均賺", f"{summary['avg_win_pct']:+.2f}%")
        e2.metric("平均賠", f"{summary['avg_loss_pct']:.2f}%")


def render_monthly_history(by_month: list[dict]):
    st.markdown(f"### {W['history_title']}")
    st.caption(f"⚠️ {W['history_caption']}")
    rows = []
    for m in by_month:
        has_signal = m["signal_count"] > 0
        rows.append({
            "月份": m["month"],
            "訊號數": m["signal_count"],
            "規則平均": (f"{m['avg_return_pct']:+.2f}%"
                       if has_signal else "—"),
            "勝率": (f"{m['win_rate_pct']:.1f}%"
                   if has_signal else "—"),
            "0050 同期": (f"{m['baseline_return_pct']:+.2f}%"
                        if m['baseline_return_pct'] is not None else "—"),
            "Alpha": (f"{m['alpha_pct']:+.2f}%"
                     if m.get('alpha_pct') is not None else "—"),
        })
    st.dataframe(pd.DataFrame(rows),
                 use_container_width=True, hide_index=True, height=400)


def render_method(method: dict):
    with st.expander("🔬 我們的方法（誠實揭露）"):
        st.markdown(f"""
- **股池**：{method['stock_pool_desc']}（共 {method['stock_pool_size']} 檔）
- **掃描頻率**：{method['frequency']}
- **持有期間**：{method['horizon']}
- **交易成本**：{method['cost_assumption']}
- **基準**：{method['baseline']}
- **未來函數防護**：{method['lookahead_protection']}

**避免回測幻覺的 5 條原則**：
1. 不挑時段（含熊市與盤整）
2. 不挑勝者（全部訊號計入）
3. 不挑指標（同時報勝率、賠率、Alpha、最大連虧）
4. 不假裝免費（扣滑價 + 手續費 + 證交稅）
5. 必比 baseline（沒比 0050 的勝率都是耍流氓）
        """)
        if PUBLIC_MODE and W["method_disclaimer"]:
            st.warning(W["method_disclaimer"])


def render_disclaimer():
    if PUBLIC_MODE:
        st.warning(
            "⚠️ **重要免責聲明**：本看板**僅供娛樂、教育與研究參考，不構成任何投資建議**。"
            "所顯示之內容為依固定規則對公開市場資料的『歷史回看』結果，"
            "**並非對任何個股之未來價格的預測或推薦**。"
            "投資有賺有賠，請依個人風險承受度與獨立判斷自行決定，"
            "本作者及網站不負任何盈虧責任。"
            "本網站無提供任何證券投資顧問業務。"
        )
    else:
        st.warning(
            "⚠️ **免責聲明**：此看板資料**僅供娛樂與研究參考，非投資建議**。"
            "過去績效不代表未來表現。投資有賺有賠，請依個人風險承受度自行判斷，"
            "本網站不負任何盈虧責任。系統訊號為**機率性陳述**，"
            "不構成對任何個股「會漲會跌」的預測。"
        )


# ============================================================
# 主入口
# ============================================================

def main():
    render_header()

    backtest = load_backtest()
    if backtest is None:
        st.error(
            "❌ 找不到 backtest_results.json 或 mock_backtest_results.json。\n\n"
            "請先執行：python3 backtest.py"
        )
        return

    if backtest.get("is_mock"):
        st.info("📌 目前顯示**模擬資料**。請執行 python3 backtest.py 產生真實回測。")

    # 市場溫度計
    if backtest.get("regime_today"):
        r = backtest["regime_today"]
        render_regime_banner(r["label"], r["msg"])
    else:
        render_regime_banner("UNKNOWN", "")

    # 今日 picks
    if backtest.get("is_mock"):
        render_today_section_from_mock(backtest.get("today_picks", []))
    else:
        col1, col2 = st.columns([4, 1])
        with col2:
            if st.button("🔄 重新掃描", use_container_width=True):
                st.cache_data.clear()
                st.rerun()
        with st.spinner("掃描台股 ~100 檔中..."):
            regime, regime_msg, l2_picks, l1_picks = scan_today()
        if l2_picks:
            render_today_section(l2_picks)
        else:
            st.warning(f"⚠️ 今日無個股通過全部條件（L2 = 0）。改顯示 L1 觀察名單（{len(l1_picks)} 檔）。")
            render_today_section(l1_picks)

    st.markdown("---")
    render_summary_metrics(backtest["summary"], backtest["horizon_days"])

    st.markdown("---")
    render_monthly_history(backtest["by_month"])

    st.markdown("---")
    render_method(backtest["method"])
    render_disclaimer()

    # Footer
    mode_tag = "🌐 公開模式" if PUBLIC_MODE else "🔒 自用模式"
    st.caption(
        f"{mode_tag} · 資料更新：{backtest['generated_at']} · "
        f"FinMind：{'✅' if FINMIND_AVAILABLE else '❌'}"
    )


if __name__ == "__main__":
    main()

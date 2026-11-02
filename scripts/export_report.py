"""把 full_states_log JSON 导出为一份可读的完整 Markdown 报告。

与 cli/main.py 的 save_report_to_disk() 相比，本脚本补齐了它没有拆出的
三个 A 股特化分析师报告（政策 / 游资 / 解禁），并把全部内容合并进**单个**
Markdown 文件（分析师 7 人 → 多空辩论 → 交易 → 风控三方 → 投资计划 →
组合经理终审 → 数据质量附录），便于直接阅读与归档。

Usage:
    # 指定 JSON 与输出路径
    python scripts/export_report.py \
        ~/.tradingagents/logs/300274/TradingAgentsStrategy_logs/full_states_log_2026-10-30.json \
        300274_2026-10-30_分析报告.md

    # 只给 JSON，输出默认写到仓库根目录 <ticker>_<date>_分析报告.md
    python scripts/export_report.py <path/to/full_states_log.json>

    # 不带参数：自动找 ~/.tradingagents/logs 下最近修改的一份导出
    python scripts/export_report.py

纯标准库实现，不依赖本仓库代码，任何 Python >= 3.10 可直接运行。
"""

import json
import sys
import time
from pathlib import Path

LOG_ROOT = Path.home() / ".tradingagents" / "logs"
REPO_ROOT = Path(__file__).resolve().parent.parent

ANALYSTS = [
    ("🏪 市场分析师（技术面）", "market_report"),
    ("💬 舆情分析师", "sentiment_report"),
    ("📰 新闻分析师", "news_report"),
    ("📊 基本面分析师", "fundamentals_report"),
    ("🏛️ 政策分析师（A股特化）", "policy_report"),
    ("🔥 游资追踪师（A股特化）", "hot_money_report"),
    ("🔓 解禁监控师（A股特化）", "lockup_report"),
]


def find_latest_log() -> Path:
    """在 ~/.tradingagents/logs 下找最近修改的 full_states_log_*.json。"""
    candidates = sorted(
        LOG_ROOT.glob("*/TradingAgentsStrategy_logs/full_states_log_*.json"),
        key=lambda p: p.stat().st_mtime,
        reverse=True,
    )
    if not candidates:
        sys.exit(f"错误：{LOG_ROOT} 下没有找到任何 full_states_log_*.json，请先跑一次分析或显式传入 JSON 路径。")
    print(f"未指定 JSON，自动选用最近一份：{candidates[0]}")
    return candidates[0]


def extract_rating(final_text: str) -> str:
    """从终审文本里抠出评级（两种写法都认）。"""
    for token in ("FINAL TRANSACTION PROPOSAL: **", "**Rating**: "):
        if token in final_text:
            seg = final_text.split(token, 1)[1]
            return seg.split("*")[0].split("\n")[0].strip() or "N/A"
    return "N/A"


def build_report(d: dict) -> str:
    ticker = d.get("company_of_interest", "?")
    date = d.get("trade_date", "?")
    final = d.get("final_trade_decision", "") or d.get("investment_plan", "")
    rating = extract_rating(final)

    sections = [
        f"# {ticker} 交易分析报告（{date}）\n\n"
        f"> ⚠️ 免责声明：本报告由 AI 自动生成，仅供学习研究，不构成任何投资建议。"
        f"投资有风险，决策请咨询持牌专业机构。\n\n"
        f"**终审评级：{rating}**"
    ]

    # I. 分析师团队（7 人）
    parts = [f"### {name}\n\n{d[key]}" for name, key in ANALYSTS if d.get(key)]
    if parts:
        sections.append("## I. 分析师团队报告（7 人）\n\n" + "\n\n---\n\n".join(parts))

    # II. 研究团队：多空辩论 + 研究经理
    deb = d.get("investment_debate_state") or {}
    parts = []
    if deb.get("bull_history"):
        parts.append(f"### 🐂 多头研究员\n\n{deb['bull_history']}")
    if deb.get("bear_history"):
        parts.append(f"### 🐻 空头研究员\n\n{deb['bear_history']}")
    if deb.get("judge_decision"):
        parts.append(f"### ⚖️ 研究经理裁决\n\n{deb['judge_decision']}")
    if parts:
        sections.append("## II. 研究团队：多空辩论与研判\n\n" + "\n\n---\n\n".join(parts))

    # III. 交易团队
    trader = d.get("trader_investment_plan") or d.get("trader_investment_decision")
    if trader:
        sections.append(f"## III. 交易团队方案\n\n### Trader（A股约束：T+1/涨跌停/手数）\n\n{trader}")

    # IV. 风险管理：三方辩论
    risk = d.get("risk_debate_state") or {}
    parts = []
    if risk.get("aggressive_history"):
        parts.append(f"### 🔥 激进派\n\n{risk['aggressive_history']}")
    if risk.get("conservative_history"):
        parts.append(f"### 🛡️ 保守派\n\n{risk['conservative_history']}")
    if risk.get("neutral_history"):
        parts.append(f"### ⚖️ 中立派\n\n{risk['neutral_history']}")
    if parts:
        sections.append("## IV. 风险管理：三方辩论\n\n" + "\n\n---\n\n".join(parts))

    # V. 投资计划
    if d.get("investment_plan"):
        sections.append(f"## V. 投资计划（Research Manager 综合）\n\n{d['investment_plan']}")

    # VI. 组合经理终审（风控裁决优先，其次终审决策字段）
    pm = risk.get("judge_decision") or d.get("final_trade_decision")
    if pm:
        sections.append(f"## VI. 组合经理终审\n\n{pm}")

    # 附录：数据质量说明
    mdt = d.get("missing_data_tasks")
    if mdt:
        items = mdt if isinstance(mdt, list) else [mdt]
        sections.append(
            "## 附录：数据质量说明\n\n本次分析存在以下数据缺口（不影响主结论，已标注于对应报告）：\n\n"
            + "\n".join(f"- {json.dumps(t, ensure_ascii=False)}" for t in items)
        )

    return "\n\n---\n\n".join(sections) + "\n"


def main() -> None:
    args = sys.argv[1:]
    if not args:
        src = find_latest_log()
        out = None
    else:
        src = Path(args[0]).expanduser()
        out = Path(args[1]).expanduser() if len(args) > 1 else None

    if not src.exists():
        sys.exit(f"错误：JSON 不存在：{src}")

    d = json.loads(src.read_text(encoding="utf-8"))
    report = build_report(d)

    if out is None:
        out = REPO_ROOT / f"{d.get('company_of_interest', '?')}_{d.get('trade_date', '?')}_分析报告.md"
    out.write_text(report, encoding="utf-8")
    print(f"已导出: {out} ({len(report)} 字符)")


if __name__ == "__main__":
    main()

"""把 TA-A 分析结果归档进 Ai-workspace-hub 的 personal wiki（胶水脚本）。

对一个 full_states_log JSON 做三件事（遵循 AWH wiki/_schema.md 契约）：

1. wiki/raw/<trade_date>-ta-<ticker>.md        完整报告原文（不改写，先保留）
2. wiki/sources/<trade_date>-ta-<ticker>.md    结构化摘要（frontmatter 按 schema 词表）
3. wiki/entities/<ticker>.md                   公司档案 create-or-merge（追加分析时间线）

并在 AWH 的 workspace/review-queue.md 追加一条 pending 待确认记录——
按 AWH 协议，wiki 沉淀属「需用户确认的动作」，脚本只入队，不自批。

Usage:
    python scripts/archive_to_workspace.py                     # 自动找最新日志，dry-run 预览
    python scripts/archive_to_workspace.py <full_states.json>  # 指定日志，dry-run 预览
    python scripts/archive_to_workspace.py <json> --apply      # 真正写入
    python scripts/archive_to_workspace.py <json> --apply --name 阳光电源

Options:
    --workspace PATH   AWH 工作区根目录（默认 ~/github/invest/Ai-workspace-hub，
                       wiki 根目录自动读 workspace-config.md 的 wiki_root）
    --name NAME        公司中文名（仅用于标题展示，可选）
    --apply            真正写入；缺省为 dry-run，只预览不落盘
    --force            raw/source 已存在时覆盖；不加时冲突即中止（不写
                       entity/queue、非零退出）；entity 时间线原位替换不叠行

纯标准库；复用同目录 export_report.py 的报告组装与评级提取。
"""

import argparse
import json
import os
import re
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from export_report import build_report, extract_rating, find_latest_log  # noqa: E402

DEFAULT_WORKSPACE = Path.home() / "Github" / "Invest" / "ai-workspace-hub"
DOMAIN = "investing"  # AWH schema 词表: investing/reading/tech/life/philosophy/meta


def atomic_write(path: Path, text: str) -> None:
    """tmp + os.replace，避免读-改-写中途截断丢资产。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(text, encoding="utf-8")
    os.replace(tmp, path)


def resolve_wiki_root(workspace: Path) -> Path:
    """读 workspace-config.md 的 wiki_root（缺省 ./wiki）。"""
    cfg = workspace / "workspace" / "workspace-config.md"
    root = "./wiki"
    if cfg.exists():
        m = re.search(r"wiki_root:\s*`([^`]+)`", cfg.read_text(encoding="utf-8"))
        if m:
            root = m.group(1).strip()
    return (workspace / root).resolve()


def section_of(text: str, name: str, limit: int = 900) -> str:
    """从 PM 终审文本里抠 **Header**: 段落（到下一个 **Header** 为止）。"""
    m = re.search(rf"\*\*{re.escape(name)}\*\*:?\s*\n?(.*?)(?=\n\*\*|\Z)", text, re.S)
    if not m:
        return ""
    out = m.group(1).strip()
    return out[:limit].rstrip() + ("…" if len(out) > limit else "")


def first_para(text: str, limit: int = 280) -> str:
    """取正文第一段实质内容（跳过标题/表格/引用/元数据行），截断。

    元数据行指报告开头的 ``**分析日期：…**`` 整行加粗、``*免责声明*`` 斜体——
    它们零信息量，不剔除时证据摘录全是日期行（``**key**：正文`` 不受影响）。
    """
    for para in re.split(r"\n\s*\n", text):
        body = []
        for ln in para.strip().splitlines():
            s = ln.strip()
            if not s or s.startswith(("#", "|", ">", "-", "*")):
                continue  # 标题/表格/引用/列表/斜体行
            if s.startswith("**") and s.endswith("**"):
                continue  # 整行加粗的元数据行（**分析日期：…**）；**key**：正文 不命中
            body.append(s)
        if body:
            out = " ".join(body)
            return out[:limit].rstrip() + ("…" if len(out) > limit else "")
    return ""


def sq(text: str) -> str:
    """压缩空白，供表格单元格用。"""
    return re.sub(r"\s+", " ", text).strip()


def build_source_page(d: dict, log_path: Path, raw_rel: str, company: str) -> str:
    ticker = d.get("company_of_interest", "?")
    date = d.get("trade_date", "?")
    final = d.get("final_trade_decision", "") or d.get("investment_plan", "")
    rating = extract_rating(final)
    exec_sum = section_of(final, "Executive Summary") or first_para(final, 600)
    today = datetime.now().strftime("%Y-%m-%d")

    analysts = [
        ("市场(技术)", "market_report"), ("舆情", "sentiment_report"),
        ("新闻", "news_report"), ("基本面", "fundamentals_report"),
        ("政策", "policy_report"), ("游资/资金", "hot_money_report"),
        ("解禁/减持", "lockup_report"),
    ]
    evidence = "\n".join(
        f"- **{label}**：{first_para(d.get(key, ''))}"
        for label, key in analysts
        if d.get(key)
    )

    deb = d.get("investment_debate_state") or {}
    bull = first_para(deb.get("bull_history", ""), 200)
    bear = first_para(deb.get("bear_history", ""), 200)

    # 待验证：从终审文本摘含验证/信号语义的行，最多 5 条（逐字摘录，不创作）
    # 排除 **Header**: 结构行（如 "Time Horizon**: 3-6个月（…业绩验证…）" 会被「验证」误命中）
    pending = []
    for line in final.splitlines():
        s = sq(line).lstrip("-*").strip()
        if "**" in s:
            continue
        if len(s) > 12 and re.search(r"(等待|确认信号|验证|观察|右侧信号|下修)", s):
            pending.append(f"- [待验证] {s[:160]}")
        if len(pending) >= 5:
            break

    parts = [f"- 来源：TA-A（TradingAgents-Astock）多 Agent 分析引擎，全流程 7 分析师→辩论→风控→终审 [本地]",
             f"- 日志：`{log_path}`；摄入日期：{today}；完整报告归档：`{raw_rel}`"]

    fm = (
        f"---\n"
        f"title: TA-A 多Agent分析：{ticker}{('（' + company + '）') if company else ''}（{date}）\n"
        f"date: {date}\n"
        f"type: source-summary\n"
        f"source_path: {log_path}\n"
        f"raw_path: {raw_rel}\n"
        f"status: processed\n"
        f"domain: [{DOMAIN}]\n"
        f"ticker: [{ticker}]\n"
        f"concepts: []\n"
        f'related: ["[[{ticker}]]"]\n'
        f"entity_salience: {{{ticker}: core}}\n"
        f"tags: [tradingagents, 多agent分析]\n"
        f"tagging: {{status: completed, schema_version: 1, provider: archive_script}}\n"
        f"---\n"
    )
    body = f"""# TA-A 多Agent分析：{ticker}（{date}）

{chr(10).join(parts)}

## 核心结论

**评级：{rating}**

{exec_sum}

## 关键证据（各分析师要点摘录）

{evidence}

## 多空辩论焦点

- 🐂 多头：{bull}
- 🐻 空头：{bear}

## 对研究的含义

- [推测] 下次分析 {ticker} 前，应先读本页与实体页时间线，复用本次辩论论点，避免从零开始。
- [推测] 终审里的右侧信号条件可直接转为 `hypothesis/` 假设，由 daily-watch 证据回填检验。
- [待验证] 本次评级在持有窗口后的实际表现，可用 TA-A `tradingagents performance` 查看（自动回填 alpha）。

## 待验证问题

{chr(10).join(pending) if pending else "- [待验证]（终审文本未含显式验证条件，待用户补充）"}

## 下一步动作

- 在 `workspace/review-queue.md` 确认本条归档（reject 即删除 raw/source/entity 三个文件）。
- 若核心论点值得跨日报跟踪，建 `hypothesis/H*.md` 并在 daily-watch 股票池中加入 {ticker}。
"""
    return fm + "\n" + body


ENTITY_FM = """---
title: {title}
type: entity
ticker: [{ticker}]
domain: [{domain}]
related: ["[[{src}]]"]
created_at: {today}
updated_at: {today}
---
"""


def build_entity_new(d: dict, source_rel: str, company: str) -> str:
    ticker = d.get("company_of_interest", "?")
    date = d.get("trade_date", "?")
    rating = extract_rating(d.get("final_trade_decision", "") or d.get("investment_plan", ""))
    today = datetime.now().strftime("%Y-%m-%d")
    title = f"{ticker}{(' ' + company) if company else ''}"
    link = source_rel.split("/")[-1].removesuffix(".md")  # wikilink 按文件名解析
    summary = section_of(d.get("final_trade_decision", ""), "Executive Summary", 160) or first_para(d.get("final_trade_decision", ""), 160)
    return (
        ENTITY_FM.format(title=title, ticker=ticker, domain=DOMAIN, src=link, today=today)
        + f"\n# {title}\n\n"
        + "## 分析时间线\n\n"
        + "| 分析日 | 评级 | 来源 | 摘要 |\n|---|---|---|---|\n"
        + f"| {date} | {rating} | [[{link}]] | {sq(summary)} |\n"
    )


def merge_entity(existing: str, d: dict, source_rel: str, force: bool = False) -> tuple[str | None, str]:
    """时间线合并；返回 (新全文, 动作描述)，None 表示幂等跳过（该来源已在时间线中）。

    force=True（--force 重归档）时原位替换旧行，评级翻转不留旧值。
    判定只认 ``|`` 开头的时间线数据行——frontmatter 的 related: ["[[…]]"] 同样
    含 wikilink，不能误伤。
    """
    date = d.get("trade_date", "?")
    rating = extract_rating(d.get("final_trade_decision", "") or d.get("investment_plan", ""))
    link = source_rel.split("/")[-1].removesuffix(".md")
    summary = section_of(d.get("final_trade_decision", ""), "Executive Summary", 160) or first_para(d.get("final_trade_decision", ""), 160)
    row = f"| {date} | {rating} | [[{link}]] | {sq(summary)} |"
    lines = existing.splitlines()
    timeline_idx = [i for i, ln in enumerate(lines) if ln.startswith("|") and f"[[{link}]]" in ln]
    if timeline_idx:
        if not force:
            return None, "跳过（该来源已在时间线中）"
        lines[timeline_idx[-1]] = row  # 原位替换：Underweight→Overweight 不叠行
        return "\n".join(lines).rstrip() + "\n", "替换时间线行（--force）"
    if "## 分析时间线" in existing:
        return existing.rstrip() + "\n" + row + "\n", "追加时间线行"
    return existing.rstrip() + "\n\n## 分析时间线\n\n| 分析日 | 评级 | 来源 | 摘要 |\n|---|---|---|---|\n" + row + "\n", "追加时间线行"


def next_queue_id(queue_text: str, today: str) -> str:
    ymd = today.replace("-", "")
    ns = [int(m.group(1)) for m in re.finditer(rf"Q-{ymd}-(\d+)", queue_text)]
    n = (max(ns) + 1) if ns else 1
    return f"Q-{ymd}-{n:02d}"


def queue_insert_at(lines: list[str]) -> int | None:
    """pending 新行的插入位置：第一张表数据区的末尾。

    只认第一张表——若按「全文件最后一个表格行」定位，pending 表之后
    再出现历史表时会把新行插进错误的表里。
    """
    sep_idx = None
    for i, ln in enumerate(lines):
        if ln.startswith("|") and re.fullmatch(r"\|[\s:|-]+\|?", ln.strip()):
            sep_idx = i
            break
        if ln.startswith("|"):  # 表头行，分隔行紧随其后
            continue
    if sep_idx is None:
        return None
    insert_at = sep_idx + 1
    for ln in lines[sep_idx + 1:]:
        if ln.startswith("|"):
            insert_at += 1
        else:
            break
    return insert_at


def main() -> None:
    ap = argparse.ArgumentParser(description="TA-A 报告归档进 AWH wiki")
    ap.add_argument("json", nargs="?", help="full_states_log JSON 路径（缺省自动找最新）")
    ap.add_argument("--workspace", type=Path, default=DEFAULT_WORKSPACE, help="AWH 工作区根目录")
    ap.add_argument("--name", default="", help="公司中文名（可选）")
    ap.add_argument("--apply", action="store_true", help="真正写入（缺省 dry-run）")
    ap.add_argument("--force", action="store_true", help="raw/source 已存在时覆盖")
    args = ap.parse_args()

    src = Path(args.json).expanduser() if args.json else find_latest_log()
    try:
        d = json.loads(src.read_text(encoding="utf-8"))
    except json.JSONDecodeError as e:
        sys.exit(f"错误：日志 JSON 解析失败（{src}）：{e}")
    ticker = str(d.get("company_of_interest") or "")
    date = str(d.get("trade_date") or "")
    if not ticker or not date:
        sys.exit(f"错误：日志缺少 company_of_interest / trade_date（{src}），拒绝产出 ?.md")
    # --name 清洗：半角 ": " 会把 title 的 YAML 值截断；裸冒号无空格时无害，只灭 ": " 序列
    company = sq(args.name).replace('"', "").replace(": ", "：")
    rating = extract_rating(d.get("final_trade_decision", "") or d.get("investment_plan", ""))

    ws = args.workspace.expanduser().resolve()
    if not ws.exists():
        sys.exit(f"错误：AWH 工作区不存在：{ws}")
    wiki = resolve_wiki_root(ws)
    stamp = f"{date}-ta-{ticker}"
    # C2：路径一律从 wiki 根派生（wiki_root 非 ./wiki 时同样正确），引用相对 ws 生成
    raw_path = wiki / "raw" / f"{stamp}.md"
    source_path = wiki / "sources" / f"{stamp}.md"
    entity_path = wiki / "entities" / f"{ticker}.md"
    try:
        raw_rel = str(raw_path.relative_to(ws))
        source_rel = str(source_path.relative_to(ws))
        entity_rel = str(entity_path.relative_to(ws))
    except ValueError:
        sys.exit(f"错误：wiki 根不在工作区内（{wiki} ⊄ {ws}），检查 workspace-config.md 的 wiki_root")
    queue_path = ws / "workspace" / "review-queue.md"

    # ── 组装内容 ──
    full_md = build_report(d)
    source_md = build_source_page(d, src, raw_rel, company)
    entity_exists = entity_path.exists()
    if entity_exists:
        entity_md, action_entity = merge_entity(
            entity_path.read_text(encoding="utf-8"), d, source_rel, force=args.force)
    else:
        action_entity = "新建实体页"
        entity_md = build_entity_new(d, source_rel, company)

    # C1：冲突未解决时一律停——不写 entity、不写 queue、不报成功
    collisions = [str(p) for p in (raw_path, source_path) if p.exists() and not args.force]
    if collisions:
        print(f"⚠️ 已存在且未加 --force，本次未写入任何文件（queue/entity 也不写）：\n  - " + "\n  - ".join(collisions))
        if args.apply:
            sys.exit(1)

    # ── 预览 ──
    print(f"归档目标：{wiki}")
    print(f"  1. {raw_rel}        完整报告 {len(full_md)} 字符" + ("（覆盖）" if raw_path.exists() and args.force else ""))
    print(f"  2. {source_rel}   摘要页（评级 {rating}）")
    print(f"  3. {entity_rel}      {action_entity}")
    print(f"  4. workspace/review-queue.md  追加 pending 待确认行")

    if not args.apply:
        print("\n[Dry-run] 未写入任何文件。确认无误后加 --apply 执行。")
        print("\n—— source 页正文预览（前 1200 字符）——")
        print(source_md[source_md.index("\n---\n", 4) + 5:][:1200])
        return

    # ── 写入（走到这里 collisions 必为空：非空时 apply 已在上方 exit(1)）──
    raw_covered = raw_path.exists()  # --force 覆盖 or 新建，决定 queue 行文案
    atomic_write(raw_path, full_md)
    atomic_write(source_path, source_md)
    if entity_md:
        atomic_write(entity_path, entity_md)

    # review-queue 追加 pending（AWH 契约：脚本可入队，不可自批）；幂等：同来源已有 pending 行则跳过
    if queue_path.exists():
        qtext = queue_path.read_text(encoding="utf-8")
        already = any(source_rel in ln and "pending" in ln for ln in qtext.splitlines())
        if already:
            print("review-queue 已有本来源的 pending 行，跳过")
        else:
            today = datetime.now().strftime("%Y-%m-%d")
            qid = next_queue_id(qtext, today)
            landing = "raw/source 已覆盖（--force）" if raw_covered else "raw/source 已落盘"
            row = (f"| {qid} | wiki | {source_rel} | 归档 TA-A 分析（{rating} @{date}），"
                   f"{landing}；entity {action_entity}；reject 请删除三文件 | archive_to_workspace.py | {today} | pending |")
            lines = qtext.rstrip().splitlines()
            insert_at = queue_insert_at(lines)
            if insert_at is None:
                sys.exit("错误：review-queue.md 未找到 pending 表（第一张表），结构异常，已中止入队")
            lines.insert(insert_at, row)
            atomic_write(queue_path, "\n".join(lines) + "\n")
            print(f"已入 review-queue：{qid}")

    print(f"\n✅ 已归档：{raw_path}\n          {source_path}")
    if entity_md:
        print(f"          {entity_path}")
    else:
        print(f"          {entity_path}（未改：{action_entity}）")
    print("后续：确认 review-queue；可选在 AWH 会话中说「复盘该实体页」触发知识沉淀。")


if __name__ == "__main__":
    main()

#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
航班座位保护可视化程序

功能：
1. 读取 config.json；
2. 调用 heuristic_seat_allocator.py 的 run_allocation；
3. 同时绘制旧机型原座位图和新机型分配结果图；
4. 通过连线汇总图展示旅客从旧座位到新座位的移动；
5. 正确处理 3-3、3-4-3、商务舱跳列和多过道布局；
6. 显示 SSR、固定新座位、紧急出口、摇篮位、靠近厕所等信息；
7. 不依赖 heuristic_seat_allocator.py 中的 Passenger 类。

默认输出：
- old_seat_allocation.png
- new_seat_allocation.png
- seat_protection_comparison.png
- seat_movement_summary.json

用法：
    python src/allocation_visualizer.py
    python src/allocation_visualizer.py --no-show
    python src/allocation_visualizer.py --config config.json --output-dir outputs
"""

from __future__ import annotations

import argparse
import colorsys
import importlib.util
import json
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple

import matplotlib.pyplot as plt
import matplotlib.patches as patches
from matplotlib.lines import Line2D
from matplotlib.patches import Rectangle


PassengerKey = Tuple[int, int]

PASSENGER_TYPE_EDGE_COLORS = {
    "normal": "#2563EB",
    "CHD": "#DC2626",
    "UM": "#EA580C",
    "WCHR": "#CA8A04",
    "BLND": "#16A34A",
    "BSCT": "#7C3AED",
    "EXST": "#9333EA",
    "CBBG": "#DB2777",
    "unoccupied": "#9CA3AF",
}

UNOCCUPIED_FILL = "#F2F2F2"
BLOCKED_FILL = "#FFF7ED"
BLOCKED_EDGE = "#F59E0B"
EXIT_HATCH = "///"
BASSINET_MARK = "B"
TOILET_MARK = "T"
EXTRA_LEGROOM_MARK = "L"


def configure_fonts() -> None:
    """尽量使用本机已有中文字体；缺失时回退到 DejaVu Sans。"""
    plt.rcParams["font.sans-serif"] = [
        "Microsoft YaHei",
        "SimHei",
        "Noto Sans CJK SC",
        "WenQuanYi Micro Hei",
        "Arial Unicode MS",
        "DejaVu Sans",
    ]
    plt.rcParams["axes.unicode_minus"] = False


def load_json(path: Path) -> dict:
    with path.open("r", encoding="utf-8") as f:
        return json.load(f)


def resolve_path(base_dir: Path, raw_path: str) -> Path:
    path = Path(raw_path)
    return path if path.is_absolute() else base_dir / path


def load_algorithm(module_path: Path):
    spec = importlib.util.spec_from_file_location("seat_allocation_module", module_path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"无法加载算法模块：{module_path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    if not hasattr(module, "run_allocation"):
        raise RuntimeError(f"{module_path} 缺少 run_allocation 函数")
    return module


def normalize_assignment(raw: Any) -> Dict[PassengerKey, str]:
    """
    兼容算法输出中的 tuple key，以及从 JSON 读取时可能出现的字符串 key。
    """
    if not isinstance(raw, Mapping):
        return {}

    result: Dict[PassengerKey, str] = {}
    for key, value in raw.items():
        if isinstance(key, tuple) and len(key) == 2:
            result[(int(key[0]), int(key[1]))] = str(value)
            continue

        if isinstance(key, list) and len(key) == 2:
            result[(int(key[0]), int(key[1]))] = str(value)
            continue

        if isinstance(key, str):
            cleaned = key.strip().strip("()[]")
            for separator in (",", ":", "|", "-"):
                parts = [p.strip() for p in cleaned.split(separator)]
                if len(parts) == 2 and all(p.lstrip("-").isdigit() for p in parts):
                    result[(int(parts[0]), int(parts[1]))] = str(value)
                    break
    return result


def normalize_blocked_by(raw: Any) -> Dict[str, List[PassengerKey]]:
    """规范算法返回的 {座位号: [(groupId, hostnum)]} 留空责任映射。"""
    if not isinstance(raw, Mapping):
        return {}
    normalized: Dict[str, List[PassengerKey]] = {}
    for seat_id, raw_keys in raw.items():
        keys: List[PassengerKey] = []
        if not isinstance(raw_keys, (list, tuple, set)):
            raw_keys = [raw_keys]
        for key in raw_keys:
            if isinstance(key, (list, tuple)) and len(key) == 2:
                keys.append((int(key[0]), int(key[1])))
        if keys:
            normalized[str(seat_id)] = keys
    return normalized


def build_passengers(groups_data: Sequence[dict]) -> Dict[PassengerKey, dict]:
    passengers: Dict[PassengerKey, dict] = {}
    for group in groups_data:
        gid = int(group["groupId"])
        for p in group.get("psrs", []):
            passengers[(gid, int(p["hostnum"]))] = p
    return passengers


def build_old_assignments(groups_data: Sequence[dict]) -> Dict[PassengerKey, str]:
    assignments: Dict[PassengerKey, str] = {}
    for group in groups_data:
        gid = int(group["groupId"])
        for p in group.get("psrs", []):
            old = p.get("oldSeat", {}).get("seatNum")
            if old:
                assignments[(gid, int(p["hostnum"]))] = str(old)
    return assignments


def seat_type(passenger: Optional[dict]) -> str:
    if passenger is None:
        return "unoccupied"
    return passenger.get("ssr") or "normal"


def infer_aisle_breaks(row_seats: Sequence[dict]) -> List[int]:
    """
    返回排序后座位列表中，哪些索引之后存在过道。

    相邻两个座位都标记为 isAisle=True 时，才可能存在过道；
    同一个座位不能同时作为左右两条过道的边界。因此对候选边界
    做非重叠匹配，可避免 2-2-2 布局的 C-D-G-H 连续过道座位
    被误判为 C/D、D/G、G/H 三条过道。

    列字母不连续只表示座位编号跳列，
    不能据此推断过道或缺失座位。
    """
    breaks: List[int] = []
    i = 0
    while i < len(row_seats) - 1:
        left = row_seats[i]
        right = row_seats[i + 1]
        if left.get("isAisle", False) and right.get("isAisle", False):
            breaks.append(i)
            i += 2
        else:
            i += 1

    return breaks


def build_layout(seats_data: Sequence[dict]) -> dict:
    rows: Dict[int, List[dict]] = defaultdict(list)
    for seat in seats_data:
        rows[int(seat["row"])].append(seat)

    for row in rows:
        rows[row].sort(key=lambda s: ord(str(s["col"])[0]))

    all_rows = sorted(rows)
    max_width = 0.0
    positions: Dict[str, Tuple[float, float]] = {}
    row_bounds: Dict[int, Tuple[float, float]] = {}
    row_breaks: Dict[int, List[float]] = {}

    aisle_gap = 0.85
    seat_step = 1.0

    for y_idx, row in enumerate(all_rows):
        seats = rows[row]
        breaks = set(infer_aisle_breaks(seats))
        x = 0.0
        break_positions: List[float] = []

        for i, seat in enumerate(seats):
            positions[seat["seatId"]] = (x, float(y_idx))
            if i in breaks:
                break_positions.append(x + 0.5 + aisle_gap / 2)
                x += seat_step + aisle_gap
            else:
                x += seat_step

        width = max(0.0, x - seat_step + 1.0)
        max_width = max(max_width, width)
        row_bounds[row] = (0.0, width)
        row_breaks[row] = break_positions

    # 各排居中，便于商务舱和经济舱宽度不同的机型展示。
    for row in all_rows:
        _, width = row_bounds[row]
        shift = (max_width - width) / 2
        for seat in rows[row]:
            sx, sy = positions[seat["seatId"]]
            positions[seat["seatId"]] = (sx + shift, sy)
        row_breaks[row] = [x + shift for x in row_breaks[row]]
        row_bounds[row] = (shift, shift + width)

    return {
        "rows": rows,
        "row_order": all_rows,
        "positions": positions,
        "row_breaks": row_breaks,
        "row_bounds": row_bounds,
        "width": max_width,
        "height": len(all_rows),
    }


def reverse_assignment(assignments: Mapping[PassengerKey, str]) -> Dict[str, PassengerKey]:
    result: Dict[str, PassengerKey] = {}
    for key, seat_id in assignments.items():
        result[str(seat_id)] = key
    return result


def passenger_label(key: PassengerKey) -> str:
    gid, hostnum = key
    return f"G{gid}-P{hostnum}"


def group_fill_color(gid: int) -> str:
    """使用黄金角分布为每个旅客组生成稳定、明亮的填充色。"""
    hue = (int(gid) * 0.618033988749895) % 1.0
    red, green, blue = colorsys.hsv_to_rgb(hue, 0.34, 0.96)
    return "#{:02X}{:02X}{:02X}".format(
        round(red * 255),
        round(green * 255),
        round(blue * 255),
    )


def draw_seatmap(
    ax,
    title: str,
    seats_data: Sequence[dict],
    groups_data: Sequence[dict],
    assignments: Mapping[PassengerKey, str],
    *,
    fixed_mode: bool,
    show_passenger_ids: bool = True,
    show_features: bool = True,
    blocked_by: Optional[Mapping[str, Sequence[PassengerKey]]] = None,
) -> dict:
    passengers = build_passengers(groups_data)
    seat_owner = reverse_assignment(assignments)
    layout = build_layout(seats_data)
    positions = layout["positions"]
    blocked_by = blocked_by or {}

    assigned_valid = 0
    for seat in seats_data:
        seat_id = str(seat["seatId"])
        x, y = positions[seat_id]
        owner = seat_owner.get(seat_id)
        passenger = passengers.get(owner) if owner else None
        blockers = list(blocked_by.get(seat_id, [])) if not owner else []
        kind = seat_type(passenger)
        face = group_fill_color(owner[0]) if owner else (BLOCKED_FILL if blockers else UNOCCUPIED_FILL)
        edge_color = BLOCKED_EDGE if blockers else PASSENGER_TYPE_EDGE_COLORS.get(kind, "#475569")
        line_width = 1.8 if owner else (1.4 if blockers else 0.6)

        if fixed_mode and owner and passenger:
            fixed_seat = passenger.get("newSeat", {}).get("seatNum")
            if fixed_seat == seat_id:
                line_width = 3.0

        hatch = EXIT_HATCH if seat.get("isExitRow", False) else None
        rect = Rectangle(
            (x - 0.42, y - 0.38),
            0.84,
            0.76,
            facecolor=face,
            edgecolor=edge_color,
            linewidth=line_width,
            hatch=hatch,
            zorder=2,
        )
        ax.add_patch(rect)

        if owner:
            assigned_valid += 1

        if owner and passenger and show_passenger_ids:
            ax.text(
                x,
                y - 0.07,
                passenger_label(owner),
                ha="center",
                va="center",
                fontsize=6.2,
                fontweight="bold",
                zorder=3,
            )

        if blockers:
            blocker_text = "/".join(f"G{gid}-P{hostnum}" for gid, hostnum in blockers)
            ax.text(
                x,
                y - 0.07,
                blocker_text,
                ha="center",
                va="center",
                fontsize=4.7,
                fontweight="bold",
                color="#9A3412",
                zorder=3,
            )
            ax.text(
                x,
                y + 0.13,
                "留空",
                ha="center",
                va="center",
                fontsize=4.2,
                color="#9A3412",
                zorder=3,
            )

        # 有旅客时座位号作为次要信息；空座位仍在中央显示座位号。
        ax.text(
            x,
            y + (0.28 if blockers else (0.20 if owner and show_passenger_ids else 0.0)),
            seat_id,
            ha="center",
            va="center",
            fontsize=4.0 if blockers else (4.5 if owner and show_passenger_ids else 5.5),
            color="#374151",
            zorder=3,
        )

        if show_features:
            marks = []
            if seat.get("hasBassinet", False):
                marks.append(BASSINET_MARK)
            if seat.get("nearToilet", seat.get("isNearToilet", False)):
                marks.append(TOILET_MARK)
            if seat.get("extraLegroom", False) and not seat.get("isExitRow", False):
                marks.append(EXTRA_LEGROOM_MARK)
            if marks:
                ax.text(
                    x + 0.34,
                    y - 0.29,
                    "".join(marks),
                    ha="right",
                    va="top",
                    fontsize=4.8,
                    fontweight="bold",
                    zorder=4,
                )

        if fixed_mode and owner and passenger:
            if passenger.get("newSeat", {}).get("seatNum") == seat_id:
                ax.text(
                    x - 0.34,
                    y + 0.30,
                    "★",
                    ha="left",
                    va="bottom",
                    fontsize=5.5,
                    fontweight="bold",
                    zorder=4,
                )

    # 过道背景带。
    for y_idx, row in enumerate(layout["row_order"]):
        for aisle_x in layout["row_breaks"][row]:
            ax.add_patch(
                Rectangle(
                    (aisle_x - 0.34, y_idx - 0.5),
                    0.68,
                    1.0,
                    facecolor="#FFFFFF",
                    edgecolor="none",
                    alpha=0.9,
                    zorder=1,
                )
            )

    ax.set_xlim(-0.8, layout["width"] + 0.8)
    ax.set_ylim(-0.8, layout["height"] - 0.2)
    ax.invert_yaxis()
    ax.set_aspect("equal")
    ax.set_xticks([])
    ax.set_yticks(range(len(layout["row_order"])))
    ax.set_yticklabels(layout["row_order"], fontsize=7)
    ax.set_ylabel("排号")
    ax.set_title(title, fontsize=13, pad=12)

    for spine in ax.spines.values():
        spine.set_visible(False)

    return {
        "layout": layout,
        "assigned_valid": assigned_valid,
        "passengers": passengers,
        "seat_owner": seat_owner,
    }


def create_legend(fig, groups_data: Sequence[dict]) -> None:
    handles: List[Any] = [
        patches.Patch(facecolor=UNOCCUPIED_FILL, edgecolor=PASSENGER_TYPE_EDGE_COLORS["unoccupied"], label="空座位"),
        patches.Patch(facecolor=BLOCKED_FILL, edgecolor=BLOCKED_EDGE, linewidth=1.5, label="逻辑留空：标注责任旅客"),
        patches.Patch(facecolor="#FFFFFF", edgecolor=PASSENGER_TYPE_EDGE_COLORS["normal"], linewidth=2, label="边框：普通旅客"),
        patches.Patch(facecolor=group_fill_color(1), edgecolor="#6B7280", label="填充：同色代表同组"),
    ]

    used_ssr = sorted({
        p.get("ssr")
        for group in groups_data
        for p in group.get("psrs", [])
        if p.get("ssr")
    })
    for ssr in used_ssr:
        handles.append(
            patches.Patch(
                facecolor="#FFFFFF",
                edgecolor=PASSENGER_TYPE_EDGE_COLORS.get(ssr, "#475569"),
                linewidth=2,
                label=f"边框：{ssr}",
            )
        )

    handles.extend([
        patches.Patch(facecolor="white", edgecolor="#111827", linewidth=3, label="固定新座位 ★（粗边框）"),
        patches.Patch(facecolor="white", edgecolor="#111827", hatch=EXIT_HATCH, label="紧急出口排"),
        Line2D([], [], marker=f"${BASSINET_MARK}$", linestyle="None", label="摇篮位"),
        Line2D([], [], marker=f"${TOILET_MARK}$", linestyle="None", label="靠近厕所"),
        Line2D([], [], marker=f"${EXTRA_LEGROOM_MARK}$", linestyle="None", label="额外腿部空间"),
    ])

    fig.legend(
        handles=handles,
        loc="center right",
        bbox_to_anchor=(0.995, 0.5),
        fontsize=8,
        title="图例",
        frameon=True,
    )


def movement_statistics(
    groups_data: Sequence[dict],
    old_assignments: Mapping[PassengerKey, str],
    new_assignments: Mapping[PassengerKey, str],
    old_seats_data: Sequence[dict],
    new_seats_data: Sequence[dict],
) -> dict:
    passengers = build_passengers(groups_data)
    old_map = {s["seatId"]: s for s in old_seats_data}
    new_map = {s["seatId"]: s for s in new_seats_data}

    stats = Counter()
    row_changes: List[int] = []
    details: List[dict] = []

    for key, p in passengers.items():
        old_id = old_assignments.get(key)
        new_id = new_assignments.get(key)

        if not new_id:
            stats["unassigned"] += 1
            details.append({
                "groupId": key[0],
                "hostnum": key[1],
                "oldSeat": old_id,
                "newSeat": None,
                "status": "unassigned",
            })
            continue

        stats["assigned"] += 1
        old = old_map.get(old_id)
        new = new_map.get(new_id)

        if old_id == new_id:
            stats["same_seat_id"] += 1
        if old and new:
            delta_row = int(new["row"]) - int(old["row"])
            row_changes.append(delta_row)
            if delta_row == 0:
                stats["same_row"] += 1
            elif delta_row < 0:
                stats["moved_forward"] += 1
            else:
                stats["moved_backward"] += 1

            if bool(old.get("isWindow")) == bool(new.get("isWindow")):
                stats["window_attribute_kept"] += 1
            if bool(old.get("isAisle")) == bool(new.get("isAisle")):
                stats["aisle_attribute_kept"] += 1
            if bool(old.get("nearToilet", False)) == bool(new.get("nearToilet", False)):
                stats["toilet_attribute_kept"] += 1

        details.append({
            "groupId": key[0],
            "hostnum": key[1],
            "ssr": p.get("ssr", ""),
            "oldSeat": old_id,
            "newSeat": new_id,
            "rowChange": (
                int(new["row"]) - int(old["row"])
                if old and new else None
            ),
            "fixed": p.get("newSeat", {}).get("seatNum") == new_id,
        })

    stats_dict = dict(stats)
    stats_dict["total_passengers"] = len(passengers)
    stats_dict["average_absolute_row_change"] = (
        sum(abs(x) for x in row_changes) / len(row_changes)
        if row_changes else 0.0
    )
    stats_dict["details"] = details
    return stats_dict


def save_single_map(
    path: Path,
    title: str,
    seats_data: Sequence[dict],
    groups_data: Sequence[dict],
    assignments: Mapping[PassengerKey, str],
    fixed_mode: bool,
    show: bool,
    blocked_by: Optional[Mapping[str, Sequence[PassengerKey]]] = None,
) -> None:
    layout = build_layout(seats_data)
    fig_width = max(10, layout["width"] * 0.76 + 3.2)
    fig_height = max(8, layout["height"] * 0.53 + 1.8)

    fig, ax = plt.subplots(figsize=(fig_width, fig_height))
    draw_seatmap(
        ax,
        title,
        seats_data,
        groups_data,
        assignments,
        fixed_mode=fixed_mode,
        blocked_by=blocked_by,
    )
    create_legend(fig, groups_data)
    fig.subplots_adjust(right=0.82)
    fig.savefig(path, dpi=220, bbox_inches="tight")
    print(f"已保存：{path}")
    if show:
        plt.show()
    plt.close(fig)


def save_comparison(
    path: Path,
    old_seats_data: Sequence[dict],
    new_seats_data: Sequence[dict],
    groups_data: Sequence[dict],
    old_assignments: Mapping[PassengerKey, str],
    new_assignments: Mapping[PassengerKey, str],
    stats: dict,
    show: bool,
    case_label: Optional[str] = None,
    new_blocked_by: Optional[Mapping[str, Sequence[PassengerKey]]] = None,
) -> None:
    old_layout = build_layout(old_seats_data)
    new_layout = build_layout(new_seats_data)
    height = max(old_layout["height"], new_layout["height"])
    width = old_layout["width"] + new_layout["width"]

    fig_width = max(16, width * 0.65 + 5.0)
    fig_height = max(10, height * 0.50 + 2.8)

    fig, axes = plt.subplots(1, 2, figsize=(fig_width, fig_height))

    draw_seatmap(
        axes[0],
        "旧机型：旅客原座位",
        old_seats_data,
        groups_data,
        old_assignments,
        fixed_mode=False,
    )
    draw_seatmap(
        axes[1],
        "新机型：座位保护结果",
        new_seats_data,
        groups_data,
        new_assignments,
        fixed_mode=True,
        blocked_by=new_blocked_by,
    )

    total = stats.get("total_passengers", 0)
    assigned = stats.get("assigned", 0)
    unassigned = stats.get("unassigned", 0)
    same_row = stats.get("same_row", 0)
    same_seat = stats.get("same_seat_id", 0)
    avg_row = stats.get("average_absolute_row_change", 0.0)

    fig.suptitle(
        "航班换机型座位保护对比" + (f" · {case_label}" if case_label else ""),
        fontsize=16,
        y=0.995,
    )
    fig.text(
        0.5,
        0.972,
        (
            f"总旅客 {total}｜已分配 {assigned}｜未分配 {unassigned}｜"
            f"同座位号 {same_seat}｜同排 {same_row}｜平均跨排 {avg_row:.2f}"
        ),
        ha="center",
        va="top",
        fontsize=10,
    )

    create_legend(fig, groups_data)
    fig.subplots_adjust(right=0.88, top=0.94, wspace=0.12)
    fig.savefig(path, dpi=220, bbox_inches="tight")
    print(f"已保存：{path}")
    if show:
        plt.show()
    plt.close(fig)


def run_visualization(
    config_path: Path,
    algorithm_path: Optional[Path] = None,
    output_dir: Optional[Path] = None,
    show: bool = True,
    groups_path: Optional[Path] = None,
    case_label: Optional[str] = None,
    allocation_result: Optional[Mapping[str, Any]] = None,
    result_path: Optional[Path] = None,
) -> dict:
    configure_fonts()

    config_path = config_path.resolve()
    base_dir = config_path.parent
    config = load_json(config_path)
    if allocation_result is not None and result_path is not None:
        raise ValueError("allocation_result 与 result_path 只能提供一个")
    file_result = load_json(result_path.resolve()) if result_path else None

    data_cfg = dict(config.get("data", {}))
    if file_result is not None:
        data_cfg.update(file_result.get("data", {}))
    old_map_path = resolve_path(base_dir, data_cfg.get("oldseatmap", "3-4-3seatmap.json"))
    new_map_path = resolve_path(base_dir, data_cfg.get("seatmap", "3-3seatmap.json"))
    groups_path = (
        groups_path.resolve()
        if groups_path
        else resolve_path(base_dir, data_cfg.get("groups", "groups.json"))
    )
    module_path = (
        algorithm_path.resolve()
        if algorithm_path
        else (base_dir / "src" / "heuristic_seat_allocator.py")
    )
    output_dir = output_dir.resolve() if output_dir else (base_dir / "outputs")
    output_dir.mkdir(parents=True, exist_ok=True)

    required_paths = [old_map_path, new_map_path, groups_path]
    if allocation_result is None and result_path is None:
        required_paths.append(module_path)
    for required in required_paths:
        if not required.exists():
            raise FileNotFoundError(f"文件不存在：{required}")

    old_seatmap = load_json(old_map_path)
    new_seatmap = load_json(new_map_path)
    groups_data = load_json(groups_path)["groups"]

    old_seats_data = old_seatmap["seats"]
    new_seats_data = new_seatmap["seats"]

    if result_path is not None:
        result = file_result or {}
        print(f"正在读取分配结果：{result_path.resolve()}")
    elif allocation_result is not None:
        result = dict(allocation_result)
    else:
        module = load_algorithm(module_path)
        weights = config.get("weights", {})
        print("正在运行座位分配算法……")
        result, _ = module.run_allocation(
            new_seats_data,
            old_seats_data,
            groups_data,
            weights,
            config,
        )
    module_score = result.get(
        "total_score",
        result.get("module_score", result.get("score_detail", {}).get(
            "total_soft_score"
        )),
    )
    new_assignments = normalize_assignment(result.get("assigned_seats", {}))
    new_blocked_by = normalize_blocked_by(result.get("blocked_by", {}))
    old_assignments = build_old_assignments(groups_data)

    stats = movement_statistics(
        groups_data,
        old_assignments,
        new_assignments,
        old_seats_data,
        new_seats_data,
    )
    stats["module_score"] = module_score

    save_single_map(
        output_dir / "old_seat_allocation.png",
        "旧机型：旅客原座位",
        old_seats_data,
        groups_data,
        old_assignments,
        fixed_mode=False,
        show=False,
    )
    save_single_map(
        output_dir / "new_seat_allocation.png",
        "新机型：座位保护结果",
        new_seats_data,
        groups_data,
        new_assignments,
        fixed_mode=True,
        show=False,
        blocked_by=new_blocked_by,
    )
    save_comparison(
        output_dir / "seat_protection_comparison.png",
        old_seats_data,
        new_seats_data,
        groups_data,
        old_assignments,
        new_assignments,
        stats,
        show=show,
        case_label=case_label,
        new_blocked_by=new_blocked_by,
    )

    summary_path = output_dir / "seat_movement_summary.json"
    summary_path.write_text(
        json.dumps(stats, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    print(f"已保存：{summary_path}")

    print(
        f"完成：总旅客 {stats['total_passengers']}，"
        f"已分配 {stats.get('assigned', 0)}，"
        f"未分配 {stats.get('unassigned', 0)}。"
    )
    return stats


def main() -> None:
    parser = argparse.ArgumentParser(description="航班座位保护结果可视化")
    parser.add_argument("--config", default="config.json", help="配置文件路径")
    parser.add_argument(
        "--algorithm",
        default=None,
        help="算法文件路径，默认 src/heuristic_seat_allocator.py",
    )
    parser.add_argument("--output-dir", default=None, help="输出目录，默认 outputs/")
    parser.add_argument("--groups", default=None, help="覆盖旅客组数据文件")
    parser.add_argument("--result", default=None, help="已有完整分配结果 JSON")
    parser.add_argument("--case-label", default=None, help="对比图中的算例标题")
    parser.add_argument("--no-show", action="store_true", help="仅保存图片，不弹出窗口")
    args = parser.parse_args()

    config_path = Path(args.config)
    algorithm_path = Path(args.algorithm) if args.algorithm else None
    output_dir = Path(args.output_dir) if args.output_dir else None
    groups_path = Path(args.groups) if args.groups else None
    result_path = Path(args.result) if args.result else None

    run_visualization(
        config_path=config_path,
        algorithm_path=algorithm_path,
        output_dir=output_dir,
        show=not args.no_show,
        groups_path=groups_path,
        case_label=args.case_label,
        result_path=result_path,
    )


if __name__ == "__main__":
    main()

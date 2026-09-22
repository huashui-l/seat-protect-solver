#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
现实化但适度简化的飞机座位图生成器

支持：
- 2-2、2-3、3-3 单通道布局
- 2-4-2、3-3-3、3-4-3 双通道布局
- 通过 JSON 配置定义其他机型、舱段和座位布局
- Business / Economy 分舱
- 紧急出口排
- 婴儿摇篮位
- 加长腿部空间
- 厕所位置与 nearToilet 字段
- 真实过道结构（通过 aisleAfter 配置）

输出字段兼容现有 allocation_evaluator.py：
seatId, row, col, isWindow, isAisle, isExitRow,
hasBassinet, extraLegroom, seatClass, nearToilet
"""

from __future__ import annotations

import argparse
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Dict, List, Mapping, Sequence, Set, Tuple


PROJECT_ROOT = Path(__file__).resolve().parent.parent


@dataclass
class CabinConfig:
    name: str
    start_row: int
    end_row: int
    seat_labels: List[str]
    aisle_after: List[str]
    seat_class: str
    bassinet_rows: Set[int]
    exit_rows: Set[int]
    extra_legroom_rows: Set[int]
    toilet_rows: Set[int]
    toilet_distance: int = 2

    @classmethod
    def from_dict(cls, raw: Mapping[str, Any]) -> "CabinConfig":
        """从自定义机型配置读取一个舱段，集合字段在此统一标准化。"""
        required = ("name", "startRow", "endRow", "seatLabels", "aisleAfter", "seatClass")
        missing = [key for key in required if key not in raw]
        if missing:
            raise ValueError(f"舱段配置缺少字段: {', '.join(missing)}")
        return cls(
            name=str(raw["name"]),
            start_row=int(raw["startRow"]),
            end_row=int(raw["endRow"]),
            seat_labels=[str(x).upper() for x in raw["seatLabels"]],
            aisle_after=[str(x).upper() for x in raw["aisleAfter"]],
            seat_class=str(raw["seatClass"]),
            bassinet_rows={int(x) for x in raw.get("bassinetRows", [])},
            exit_rows={int(x) for x in raw.get("exitRows", [])},
            extra_legroom_rows={int(x) for x in raw.get("extraLegroomRows", [])},
            toilet_rows={int(x) for x in raw.get("toiletRows", [])},
            toilet_distance=int(raw.get("toiletDistance", 2)),
        )


def make_seat(
    row: int,
    col: str,
    seat_labels: Sequence[str],
    aisle_after: Sequence[str],
    seat_class: str,
    exit_rows: Set[int],
    bassinet_rows: Set[int],
    extra_legroom_rows: Set[int],
    toilet_rows: Set[int],
    toilet_distance: int,
) -> dict:
    idx = seat_labels.index(col)

    # 靠窗：最左与最右
    is_window = idx == 0 or idx == len(seat_labels) - 1

    # 靠过道：位于过道两侧
    aisle_pairs = set()
    for left_col in aisle_after:
        left_idx = seat_labels.index(left_col)
        if left_idx + 1 < len(seat_labels):
            aisle_pairs.add(left_col)
            aisle_pairs.add(seat_labels[left_idx + 1])

    is_aisle = col in aisle_pairs
    is_exit = row in exit_rows
    has_bassinet = row in bassinet_rows and (is_window or is_aisle)
    extra_legroom = row in extra_legroom_rows or is_exit

    # nearToilet 采用布尔值：距任一厕所排号不超过阈值
    near_toilet = any(abs(row - trow) <= toilet_distance for trow in toilet_rows)

    return {
        "seatId": f"{row}{col}",
        "row": row,
        "col": col,
        "isWindow": is_window,
        "isAisle": is_aisle,
        "isExitRow": is_exit,
        "hasBassinet": has_bassinet,
        "extraLegroom": extra_legroom,
        "seatClass": seat_class,
        "nearToilet": near_toilet,
    }


def generate_aircraft(
    aircraft_model: str,
    cabins: Sequence[CabinConfig],
    toilets: List[dict],
) -> dict:
    seats = []
    all_labels = []
    total_rows = 0

    for cabin in cabins:
        total_rows = max(total_rows, cabin.end_row)
        for label in cabin.seat_labels:
            if label not in all_labels:
                all_labels.append(label)

        for row in range(cabin.start_row, cabin.end_row + 1):
            for col in cabin.seat_labels:
                seats.append(
                    make_seat(
                        row=row,
                        col=col,
                        seat_labels=cabin.seat_labels,
                        aisle_after=cabin.aisle_after,
                        seat_class=cabin.seat_class,
                        exit_rows=cabin.exit_rows,
                        bassinet_rows=cabin.bassinet_rows,
                        extra_legroom_rows=cabin.extra_legroom_rows,
                        toilet_rows=cabin.toilet_rows,
                        toilet_distance=cabin.toilet_distance,
                    )
                )

    return {
        "aircraftModel": aircraft_model,
        "rows": total_rows,
        "totalSeats": len(seats),
        "seatLabels": all_labels,
        "toilets": toilets,
        "seats": seats,
    }


def load_aircraft_config(path: Path) -> Tuple[str, dict]:
    """加载自定义机型 JSON，返回建议文件名和生成后的标准座位图。"""
    raw = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        raise ValueError("自定义机型配置必须是 JSON 对象")
    model = str(raw.get("aircraftModel", "")).strip()
    if not model:
        raise ValueError("自定义机型配置缺少 aircraftModel")
    cabin_items = raw.get("cabins")
    if not isinstance(cabin_items, list) or not cabin_items:
        raise ValueError("自定义机型配置必须包含非空 cabins 数组")
    toilets = raw.get("toilets", [])
    if not isinstance(toilets, list):
        raise ValueError("toilets 必须是数组")
    cabins = [CabinConfig.from_dict(item) for item in cabin_items]
    filename = str(raw.get("outputFilename", f"{model}-seatmap.json"))
    return filename, generate_aircraft(model, cabins, toilets)


def preset_regional_22() -> dict:
    """典型支线机：前舱 1-1，经济舱 2-2。"""
    cabins = [
        CabinConfig(
            name="Business", start_row=1, end_row=3,
            seat_labels=["A", "D"], aisle_after=["A"], seat_class="Business",
            bassinet_rows={1}, exit_rows=set(), extra_legroom_rows={1},
            toilet_rows={1, 26},
        ),
        CabinConfig(
            name="Economy", start_row=4, end_row=26,
            seat_labels=["A", "C", "D", "F"], aisle_after=["C"], seat_class="Economy",
            bassinet_rows={4}, exit_rows={12, 13}, extra_legroom_rows={4, 12, 13},
            toilet_rows={1, 26},
        ),
    ]
    toilets = [{"position": "front", "nearRow": 1}, {"position": "rear", "nearRow": 26}]
    return generate_aircraft("Generic-RJ-26-2-2", cabins, toilets)


def preset_narrowbody_23() -> dict:
    """典型五座单通道机：前舱 2-2，经济舱 2-3。"""
    cabins = [
        CabinConfig(
            name="Business", start_row=1, end_row=3,
            seat_labels=["A", "C", "D", "F"], aisle_after=["C"], seat_class="Business",
            bassinet_rows={1}, exit_rows=set(), extra_legroom_rows={1},
            toilet_rows={1, 28},
        ),
        CabinConfig(
            name="Economy", start_row=4, end_row=28,
            seat_labels=["A", "C", "D", "E", "F"], aisle_after=["C"], seat_class="Economy",
            bassinet_rows={4}, exit_rows={11, 12}, extra_legroom_rows={4, 11, 12},
            toilet_rows={1, 28},
        ),
    ]
    toilets = [{"position": "front", "nearRow": 1}, {"position": "rear", "nearRow": 28}]
    return generate_aircraft("Generic-NB-28-2-3", cabins, toilets)


def preset_narrowbody_33() -> dict:
    """
    典型单通道 3-3：
    - 1-4 排商务舱，2-2 布局
    - 5-32 排经济舱，3-3 布局
    - 12、13 排紧急出口
    - 厕所在前舱和后舱
    """
    cabins = [
        CabinConfig(
            name="Business",
            start_row=1,
            end_row=4,
            seat_labels=["A", "C", "D", "F"],
            aisle_after=["C"],
            seat_class="Business",
            bassinet_rows={1},
            exit_rows=set(),
            extra_legroom_rows={1},
            toilet_rows={1, 32},
            toilet_distance=2,
        ),
        CabinConfig(
            name="Economy",
            start_row=5,
            end_row=32,
            seat_labels=["A", "B", "C", "D", "E", "F"],
            aisle_after=["C"],
            seat_class="Economy",
            bassinet_rows={5},
            exit_rows={12, 13},
            extra_legroom_rows={5, 12, 13},
            toilet_rows={1, 32},
            toilet_distance=2,
        ),
    ]
    toilets = [
        {"position": "front", "nearRow": 1},
        {"position": "rear", "nearRow": 32},
    ]
    return generate_aircraft("Generic-NB-32-3-3", cabins, toilets)


def preset_widebody_343() -> dict:
    """
    典型双通道 3-4-3：
    - 1-6 排商务舱，2-2-2 简化布局
    - 7-36 排经济舱，3-4-3 布局
    - 16、17、28、29 排紧急出口
    - 厕所在前、中、后舱
    """
    cabins = [
        CabinConfig(
            name="Business",
            start_row=1,
            end_row=6,
            seat_labels=["A", "C", "D", "G", "H", "K"],
            aisle_after=["C", "G"],
            seat_class="Business",
            bassinet_rows={1},
            exit_rows=set(),
            extra_legroom_rows={1},
            toilet_rows={1, 18, 36},
            toilet_distance=2,
        ),
        CabinConfig(
            name="Economy",
            start_row=7,
            end_row=36,
            seat_labels=["A", "B", "C", "D", "E", "F", "G", "H", "J", "K"],
            aisle_after=["C", "G"],
            seat_class="Economy",
            bassinet_rows={7, 18},
            exit_rows={16, 17, 28, 29},
            extra_legroom_rows={7, 16, 17, 18, 28, 29},
            toilet_rows={1, 18, 36},
            toilet_distance=2,
        ),
    ]
    toilets = [
        {"position": "front", "nearRow": 1},
        {"position": "mid", "nearRow": 18},
        {"position": "rear", "nearRow": 36},
    ]
    return generate_aircraft("Generic-WB-36-3-4-3", cabins, toilets)


def preset_widebody_242() -> dict:
    """典型双通道机：商务舱 2-2-2，经济舱 2-4-2。"""
    cabins = [
        CabinConfig(
            name="Business", start_row=1, end_row=6,
            seat_labels=["A", "C", "D", "G", "H", "K"], aisle_after=["C", "G"],
            seat_class="Business", bassinet_rows={1}, exit_rows=set(),
            extra_legroom_rows={1}, toilet_rows={1, 19, 37},
        ),
        CabinConfig(
            name="Economy", start_row=7, end_row=37,
            seat_labels=["A", "C", "D", "E", "F", "G", "H", "K"],
            aisle_after=["C", "G"], seat_class="Economy",
            bassinet_rows={7, 19}, exit_rows={16, 17, 29, 30},
            extra_legroom_rows={7, 16, 17, 19, 29, 30}, toilet_rows={1, 19, 37},
        ),
    ]
    toilets = [
        {"position": "front", "nearRow": 1},
        {"position": "mid", "nearRow": 19},
        {"position": "rear", "nearRow": 37},
    ]
    return generate_aircraft("Generic-WB-37-2-4-2", cabins, toilets)


def preset_widebody_333() -> dict:
    """典型双通道机：商务舱 2-2-2，经济舱 3-3-3。"""
    cabins = [
        CabinConfig(
            name="Business", start_row=1, end_row=7,
            seat_labels=["A", "C", "D", "G", "H", "K"], aisle_after=["C", "G"],
            seat_class="Business", bassinet_rows={1}, exit_rows=set(),
            extra_legroom_rows={1}, toilet_rows={1, 20, 40},
        ),
        CabinConfig(
            name="Economy", start_row=8, end_row=40,
            seat_labels=["A", "B", "C", "D", "E", "F", "G", "H", "J"],
            aisle_after=["C", "F"], seat_class="Economy",
            bassinet_rows={8, 20}, exit_rows={18, 19, 30, 31},
            extra_legroom_rows={8, 18, 19, 20, 30, 31}, toilet_rows={1, 20, 40},
        ),
    ]
    toilets = [
        {"position": "front", "nearRow": 1},
        {"position": "mid", "nearRow": 20},
        {"position": "rear", "nearRow": 40},
    ]
    return generate_aircraft("Generic-WB-40-3-3-3", cabins, toilets)


PRESETS: Dict[str, Tuple[str, Callable[[], dict]]] = {
    "2-2": ("2-2seatmap.json", preset_regional_22),
    "2-3": ("2-3seatmap.json", preset_narrowbody_23),
    "3-3": ("3-3seatmap.json", preset_narrowbody_33),
    "2-4-2": ("2-4-2seatmap.json", preset_widebody_242),
    "3-3-3": ("3-3-3seatmap.json", preset_widebody_333),
    "3-4-3": ("3-4-3seatmap.json", preset_widebody_343),
}


def validate(seatmap: dict) -> List[str]:
    errors = []
    seen = set()

    if not isinstance(seatmap.get("seats"), list) or not seatmap["seats"]:
        return ["seats 必须是非空数组"]

    for seat in seatmap["seats"]:
        sid = seat["seatId"]
        if sid in seen:
            errors.append(f"重复座位号: {sid}")
        seen.add(sid)

        if seat["isWindow"] and seat["isAisle"] and len(seatmap["seatLabels"]) > 2:
            # 小型商务布局可能同时成立，但本生成器预设不会这样
            pass

        required = [
            "seatId", "row", "col", "isWindow", "isAisle",
            "isExitRow", "hasBassinet", "extraLegroom",
            "seatClass", "nearToilet",
        ]
        for key in required:
            if key not in seat:
                errors.append(f"{sid} 缺少字段 {key}")

        if seat["hasBassinet"] and seat["isExitRow"]:
            errors.append(f"{sid} 同时为摇篮位和紧急出口位")

    if seatmap["totalSeats"] != len(seatmap["seats"]):
        errors.append("totalSeats 与 seats 数量不一致")

    rows: Dict[int, List[dict]] = {}
    for seat in seatmap["seats"]:
        rows.setdefault(int(seat["row"]), []).append(seat)
    for row, seats in rows.items():
        labels = [seat["col"] for seat in seats]
        if len(labels) != len(set(labels)):
            errors.append(f"第 {row} 排存在重复列号")
        aisle_count = sum(bool(seat["isAisle"]) for seat in seats)
        if len(seats) > 1 and aisle_count == 0:
            errors.append(f"第 {row} 排没有过道侧座位")

    return errors


def main() -> None:
    parser = argparse.ArgumentParser(description="生成预设或自定义飞机座位图")
    parser.add_argument(
        "--layout",
        choices=[*PRESETS, "both", "all"],
        default="both",
        help="预设布局；both 保持兼容，仅生成原有两种布局，all 生成全部预设",
    )
    parser.add_argument("--output-dir", default=str(PROJECT_ROOT / "data"))
    parser.add_argument("--config", type=Path, help="自定义机型 JSON 配置；提供后忽略 --layout")
    parser.add_argument("--output-filename", help="覆盖自定义配置中的输出文件名")
    parser.add_argument("--list-layouts", action="store_true", help="列出可用预设后退出")
    args = parser.parse_args()

    if args.list_layouts:
        for name, (filename, factory) in PRESETS.items():
            seatmap = factory()
            print(f"{name:7} {seatmap['aircraftModel']:24} {seatmap['totalSeats']:3} seats -> {filename}")
        return

    out_dir = Path(args.output_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    outputs: Dict[str, dict] = {}
    if args.config:
        filename, seatmap = load_aircraft_config(args.config)
        outputs[args.output_filename or filename] = seatmap
    else:
        selected = (
            ["3-3", "3-4-3"] if args.layout == "both"
            else list(PRESETS) if args.layout == "all"
            else [args.layout]
        )
        for name in selected:
            filename, factory = PRESETS[name]
            outputs[filename] = factory()

    for filename, seatmap in outputs.items():
        errors = validate(seatmap)
        if errors:
            raise RuntimeError(
                f"{filename} 校验失败:\n" + "\n".join(errors)
            )

        path = out_dir / filename
        path.write_text(
            json.dumps(seatmap, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        print(f"已生成 {path}，共 {seatmap['totalSeats']} 个座位")


if __name__ == "__main__":
    main()

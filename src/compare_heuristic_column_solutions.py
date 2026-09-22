#!/usr/bin/env python3
"""Compare one saved column-generation solution with its heuristic warm start."""

from __future__ import annotations

import argparse
import ast
import importlib.util
import json
import sys
from collections import defaultdict
from pathlib import Path
from typing import Any, Dict, Mapping, Tuple

try:
    from . import allocation_evaluator as evaluator
except ImportError:
    import allocation_evaluator as evaluator


PROJECT_ROOT = Path(__file__).resolve().parent.parent
PassengerKey = Tuple[int, int]


def _read_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def _assignments(raw: Mapping[Any, Any]) -> Dict[PassengerKey, str]:
    result: Dict[PassengerKey, str] = {}
    for raw_key, seat_id in raw.items():
        key = raw_key
        if isinstance(key, str):
            key = ast.literal_eval(key)
        if not isinstance(key, (tuple, list)) or len(key) != 2:
            raise ValueError(f"无法解析旅客主键：{raw_key!r}")
        result[(int(key[0]), int(key[1]))] = str(seat_id)
    return result


def _passenger_category(passenger: Mapping[str, Any]) -> str:
    mandatory = passenger.get("mandatoryRule", {}) or {}
    if passenger.get("newSeat"):
        return "固定座位"
    if mandatory.get("needBothSideEmpty", "N") == "Y":
        return "双侧保护空座"
    if mandatory.get("needSingleSideEmpty", "N") == "Y":
        return "单侧保护空座"
    if passenger.get("needCared", "N") == "Y":
        return "需陪护SSR"
    if passenger.get("ssr"):
        return f"SSR-{passenger['ssr']}"
    return "普通旅客"


def _fmt(value: float) -> str:
    return f"{value:.3f}"


def _movement_components(
    heuristic: Mapping[PassengerKey, str],
    column: Mapping[PassengerKey, str],
) -> list[tuple[int, ...]]:
    """Return connected group components induced by cross-group seat claims."""
    owner = {seat_id: key[0] for key, seat_id in heuristic.items()}
    moved_groups = {
        key[0] for key in heuristic
        if heuristic[key] != column[key]
    }
    adjacency = {group_id: set() for group_id in moved_groups}
    for key, target_seat in column.items():
        if heuristic[key] == target_seat:
            continue
        blocking_group = owner.get(target_seat)
        if blocking_group is None or blocking_group == key[0]:
            continue
        adjacency.setdefault(key[0], set()).add(blocking_group)
        adjacency.setdefault(blocking_group, set()).add(key[0])
    components: list[tuple[int, ...]] = []
    unseen = set(adjacency)
    while unseen:
        root = min(unseen)
        pending = [root]
        component = set()
        while pending:
            group_id = pending.pop()
            if group_id in component:
                continue
            component.add(group_id)
            pending.extend(adjacency.get(group_id, ()))
        unseen.difference_update(component)
        components.append(tuple(sorted(component)))
    return sorted(components, key=lambda item: (-len(item), item))


def main() -> None:
    parser = argparse.ArgumentParser(
        description="按评分分项、同行组和旅客比较启发式与列生成整数解"
    )
    parser.add_argument("--config", type=Path, default=PROJECT_ROOT / "config.json")
    parser.add_argument(
        "--old-seatmap", type=Path,
        help="覆盖config.data.oldseatmap；相对路径按项目根目录解析",
    )
    parser.add_argument(
        "--new-seatmap", type=Path,
        help="覆盖config.data.seatmap；相对路径按项目根目录解析",
    )
    parser.add_argument("--groups", type=Path, required=True)
    parser.add_argument("--column-result", type=Path, required=True)
    parser.add_argument(
        "--heuristic-module",
        type=Path,
        help=(
            "待比较的启发式Python文件；省略时使用列生成同次运行保存的"
            "heuristic warm start"
        ),
    )
    parser.add_argument(
        "--business-time-limit",
        type=float,
        help="重跑启发式时按统一实验规则覆盖并缩放阶段预算",
    )
    parser.add_argument(
        "--inject-reference-patterns",
        action="store_true",
        help=(
            "诊断限定主问题：把列池整数解的组模式注入启发式模式池；"
            "该选项会禁用Local Branching，不可用于正式评分"
        ),
    )
    parser.add_argument(
        "--inject-reference-group-ids",
        help="诊断时仅注入逗号分隔的指定组参考模式",
    )
    parser.add_argument(
        "--disable-pattern-local-branching",
        action="store_true",
        help="诊断全局限制主问题，不改变正式配置",
    )
    parser.add_argument(
        "--disable-global-full-resource-blocks",
        action="store_true",
        help="诊断满载保护压力例的全舱排窗模式，不改变正式配置",
    )
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()

    config = _read_json(args.config.resolve())
    if args.disable_pattern_local_branching:
        config.setdefault("algorithm", {})[
            "enable_pattern_local_branching"
        ] = False
    if args.disable_global_full_resource_blocks:
        config.setdefault("algorithm", {})[
            "enable_global_full_resource_blocks"
        ] = False
    if args.business_time_limit is not None:
        if args.business_time_limit <= 0:
            raise ValueError("--business-time-limit必须为正数")
        try:
            from .rerun_heuristic_24cases import override_heuristic_time_limit
        except ImportError:
            from rerun_heuristic_24cases import override_heuristic_time_limit
        override_heuristic_time_limit(config, args.business_time_limit)
    data = config["data"]
    def seatmap_path(override: Path | None, configured: str) -> Path:
        path = override or Path(configured)
        return path if path.is_absolute() else PROJECT_ROOT / path

    old_seats = _read_json(
        seatmap_path(args.old_seatmap, data["oldseatmap"])
    )["seats"]
    new_seats = _read_json(
        seatmap_path(args.new_seatmap, data["seatmap"])
    )["seats"]
    groups = _read_json(args.groups.resolve())["groups"]
    result = _read_json(args.column_result.resolve())
    if args.heuristic_module is not None:
        config["_diagnostic_reference_assignments"] = _assignments(
            result["assigned_seats"]
        )
        config["_diagnostic_reference_blocked_by"] = result.get(
            "blocked_by", {}
        )
        if args.inject_reference_patterns or args.inject_reference_group_ids:
            config["_diagnostic_inject_reference_patterns"] = True
            if args.inject_reference_group_ids:
                config["_diagnostic_reference_group_ids"] = {
                    int(value.strip())
                    for value in args.inject_reference_group_ids.split(",")
                    if value.strip()
                }
            config.setdefault("algorithm", {})[
                "enable_pattern_local_branching"
            ] = False
        module_path = args.heuristic_module.resolve()
        sys.path.insert(0, str(module_path.parent))
        sys.path.insert(1, str(PROJECT_ROOT / "src"))
        spec = importlib.util.spec_from_file_location(
            "comparison_heuristic", module_path
        )
        if spec is None or spec.loader is None:
            raise ImportError(f"无法加载启发式算法：{module_path}")
        module = importlib.util.module_from_spec(spec)
        sys.modules[spec.name] = module
        spec.loader.exec_module(module)
        heuristic_result, _ = module.run_allocation(
            new_seats,
            old_seats,
            groups,
            config.get("weights", {}),
            config,
        )
        heuristic_raw = heuristic_result.get("assigned_seats", {})
        heuristic_diagnostics = heuristic_result.get("diagnostics", {})
    else:
        heuristic_raw = (
            result.get("diagnostics", {})
            .get("heuristic_warm_start", {})
            .get("assignments")
        )
        if not heuristic_raw:
            raise ValueError(
                "列生成结果没有保存 heuristic_warm_start.assignments；"
                "请指定 --heuristic-module，或使用更新后的列生成程序重新运行"
            )
        heuristic_diagnostics = {}
    solutions = {
        "启发式": _assignments(heuristic_raw),
        "列生成": _assignments(result["assigned_seats"]),
    }

    passenger_map = {
        (int(group["groupId"]), int(passenger["hostnum"])): passenger
        for group in groups
        for passenger in group.get("psrs", [])
    }
    group_map = {int(group["groupId"]): group for group in groups}
    new_seat_map = {str(seat["seatId"]): seat for seat in new_seats}
    scorer = evaluator.IncrementalSoftScorer(
        new_seats, old_seats, groups, config.get("weights", {}), config
    )
    components = {
        name: scorer.components(assignments)
        for name, assignments in solutions.items()
    }

    for name, assignments in solutions.items():
        violations, unassigned, _ = evaluator.count_hard_constraint_violations(
            new_seats, groups, assignments, config
        )
        if violations or unassigned:
            raise ValueError(
                f"{name}不是完整可行解：violations={violations}, "
                f"unassigned={unassigned}"
            )

    def individual(key: PassengerKey, seat_id: str) -> float:
        return sum(scorer._individual(key, seat_id).values())

    baby_by_group: Dict[str, Dict[int, float]] = {
        name: defaultdict(float) for name in solutions
    }
    for name, assignments in solutions.items():
        for infant_key, infant_seat in assignments.items():
            if passenger_map[infant_key].get("ssr") != "BSCT":
                continue
            for other_key, other_seat in assignments.items():
                if other_key[0] == infant_key[0]:
                    continue
                baby_by_group[name][infant_key[0]] += (
                    evaluator.baby_interference_score(
                        infant_seat,
                        other_seat,
                        scorer.new_topology,
                        config.get("weights", {}),
                        config,
                    )
                )

    group_rows = []
    passenger_rows = []
    category_rows: Dict[str, Dict[str, float]] = defaultdict(
        lambda: {"count": 0, "changed": 0, "delta": 0.0}
    )
    for key, passenger in passenger_map.items():
        heuristic_seat = solutions["启发式"][key]
        column_seat = solutions["列生成"][key]
        delta = individual(key, column_seat) - individual(key, heuristic_seat)
        category = _passenger_category(passenger)
        category_rows[category]["count"] += 1
        category_rows[category]["changed"] += heuristic_seat != column_seat
        category_rows[category]["delta"] += delta
        if heuristic_seat != column_seat:
            passenger_rows.append(
                {
                    "key": key,
                    "category": category,
                    "ssr": passenger.get("ssr", ""),
                    "old": passenger.get("oldSeat", {}).get("seatNum", ""),
                    "heuristic": heuristic_seat,
                    "column": column_seat,
                    "delta": delta,
                }
            )

    for group_id, group in group_map.items():
        keys = [
            (group_id, int(passenger["hostnum"]))
            for passenger in group.get("psrs", [])
        ]
        row: Dict[str, Any] = {
            "group": group_id,
            "size": len(keys),
            "types": ",".join(
                sorted({_passenger_category(passenger) for passenger in group["psrs"]})
            ),
            "changed": sum(
                solutions["启发式"][key] != solutions["列生成"][key]
                for key in keys
            ),
        }
        for name, assignments in solutions.items():
            seats = [assignments[key] for key in keys]
            rows = [int(new_seat_map[seat_id]["row"]) for seat_id in seats]
            individual_score = sum(individual(key, assignments[key]) for key in keys)
            compact_score = scorer._compactness(seats)
            baby_score = baby_by_group[name].get(group_id, 0.0)
            row[f"{name}_seats"] = ",".join(seats)
            row[f"{name}_individual"] = individual_score
            row[f"{name}_compact"] = compact_score
            row[f"{name}_baby"] = baby_score
            row[f"{name}_total"] = individual_score + compact_score + baby_score
            row[f"{name}_row_span"] = max(rows) - min(rows) if rows else 0
            row[f"{name}_row_count"] = len(set(rows))
        row["individual_delta"] = row["列生成_individual"] - row["启发式_individual"]
        row["compact_delta"] = row["列生成_compact"] - row["启发式_compact"]
        row["baby_delta"] = row["列生成_baby"] - row["启发式_baby"]
        row["delta"] = row["列生成_total"] - row["启发式_total"]
        row["row_span_delta"] = row["列生成_row_span"] - row["启发式_row_span"]
        row["row_count_delta"] = row["列生成_row_count"] - row["启发式_row_count"]
        group_rows.append(row)

    total_delta = (
        components["列生成"]["total_soft_score"]
        - components["启发式"]["total_soft_score"]
    )
    changed_passengers = sum(
        solutions["启发式"][key] != solutions["列生成"][key]
        for key in passenger_map
    )
    movement_components = _movement_components(
        solutions["启发式"], solutions["列生成"]
    )
    group_row_by_id = {row["group"]: row for row in group_rows}
    lines = [
        f"# {args.groups.stem}：启发式与列生成分配差异",
        "",
        "## 总体结果",
        "",
        f"- 启发式分数：`{_fmt(components['启发式']['total_soft_score'])}`",
        f"- 列生成整数解分数：`{_fmt(components['列生成']['total_soft_score'])}`",
        f"- 列生成改善：`{_fmt(total_delta)}`",
        f"- 换座旅客：`{changed_passengers}/{len(passenger_map)}`",
        f"- 发生变化的同行组：`{sum(any(solutions['启发式'][key] != solutions['列生成'][key] for key in passenger_map if key[0] == group_id) for group_id in group_map)}/{len(group_map)}`",
        f"- 跨组座位占用冲突组件：`{len(movement_components)}`，最大组件 `{max((len(component) for component in movement_components), default=0)}` 组",
        "",
        "## 评分分项",
        "",
        "| 分项 | 启发式 | 列生成 | 改善 |",
        "|---|---:|---:|---:|",
    ]
    labels = {
        "score_s": "旧新座位距离",
        "score_v": "座位价值",
        "score_p": "偏好匹配",
        "score_c": "同行组紧凑度",
        "score_b": "婴儿影响",
    }
    for key, label in labels.items():
        lines.append(
            f"| {label} | {_fmt(components['启发式'][key])} | "
            f"{_fmt(components['列生成'][key])} | "
            f"{_fmt(components['列生成'][key] - components['启发式'][key])} |"
        )

    lines.extend([
        "",
        "## 旅客类型（仅个体可加分项）",
        "",
        "| 类型 | 人数 | 换座人数 | 个体分改善 |",
        "|---|---:|---:|---:|",
    ])
    for category, values in sorted(category_rows.items(), key=lambda item: item[1]["delta"]):
        lines.append(
            f"| {category} | {int(values['count'])} | {int(values['changed'])} | "
            f"{_fmt(values['delta'])} |"
        )

    lines.extend([
        "",
        "## 跨组座位占用冲突组件",
        "",
        "| 组件 | 组数 | 换座旅客 | 组件总改善 |",
        "|---|---:|---:|---:|",
    ])
    for component in movement_components[:15]:
        component_set = set(component)
        component_changed = sum(
            heuristic_seat != solutions["列生成"][key]
            for key, heuristic_seat in solutions["启发式"].items()
            if key[0] in component_set
        )
        component_delta = sum(
            group_row_by_id[group_id]["delta"]
            for group_id in component
        )
        lines.append(
            f"| {','.join(map(str, component))} | {len(component)} | "
            f"{component_changed} | {_fmt(component_delta)} |"
        )

    def add_group_table(title: str, rows: list[dict]) -> None:
        lines.extend([
            "", title, "",
            "| 组 | 人数 | 类型 | 换座 | H跨排 | I跨排 | 总改善 | 紧凑度改善 | 个体改善 | 婴儿改善 |",
            "|---:|---:|---|---:|---:|---:|---:|---:|---:|---:|",
        ])
        for row in rows:
            lines.append(
                f"| {row['group']} | {row['size']} | {row['types']} | {row['changed']} | "
                f"{row['启发式_row_span']} | {row['列生成_row_span']} | "
                f"{_fmt(row['delta'])} | {_fmt(row['compact_delta'])} | "
                f"{_fmt(row['individual_delta'])} | {_fmt(row['baby_delta'])} |"
            )

    add_group_table(
        "## 改善最大的同行组",
        sorted(group_rows, key=lambda row: row["delta"], reverse=True)[:15],
    )
    add_group_table(
        "## 列生成中反而下降的同行组",
        [row for row in sorted(group_rows, key=lambda row: row["delta"]) if row["delta"] < -1e-9][:10],
    )

    def add_passenger_table(title: str, rows: list[dict]) -> None:
        lines.extend([
            "", title, "",
            "| 旅客 | 类型 | SSR | 原座位 | 启发式 | 列生成 | 个体分改善 |",
            "|---|---|---|---|---|---|---:|",
        ])
        for row in rows:
            lines.append(
                f"| {row['key']} | {row['category']} | {row['ssr']} | {row['old']} | "
                f"{row['heuristic']} | {row['column']} | {_fmt(row['delta'])} |"
            )

    add_passenger_table(
        "## 个体分改善最大的旅客",
        sorted(passenger_rows, key=lambda row: row["delta"], reverse=True)[:20],
    )
    add_passenger_table(
        "## 个体分下降最大的旅客",
        sorted(passenger_rows, key=lambda row: row["delta"])[:20],
    )
    lines.extend([
        "",
        "> 同行组总改善包含个体分、紧凑度及以 BSCT 所在组归属的婴儿影响；"
        "旅客表只列可按旅客分解的个体分，不能单独解释紧凑度。",
        "",
    ])
    args.output.resolve().parent.mkdir(parents=True, exist_ok=True)
    args.output.resolve().write_text("\n".join(lines), encoding="utf-8")
    restricted_runs = {}
    reference_coverage = {}
    pattern_generation = {}
    elite_pattern_summary = {}
    stage_statistics = {}
    cabin_runs = (
        heuristic_diagnostics.get("cabin_decomposition", {})
        .get("cabins", {})
    )
    if cabin_runs:
        for cabin, cabin_run in cabin_runs.items():
            restricted = cabin_run.get("diagnostics", {}).get(
                "restricted_pattern_mip", {}
            )
            restricted_runs[cabin] = {
                "radius_history": restricted.get("local_branching", {}).get(
                    "radius_history", []
                ),
                "accepted_attempts": restricted.get("accepted_attempts", []),
            }
            reference_coverage[cabin] = cabin_run.get("diagnostics", {}).get(
                "elite_reference_coverage"
            )
            pattern_generation[cabin] = cabin_run.get("diagnostics", {}).get(
                "structured_pattern_generation"
            )
            elite_pattern_summary[cabin] = cabin_run.get("diagnostics", {}).get(
                "elite_pattern_summary"
            )
            stage_statistics[cabin] = cabin_run.get("diagnostics", {}).get(
                "stage_statistics"
            )
    elif heuristic_diagnostics:
        restricted = heuristic_diagnostics.get("restricted_pattern_mip", {})
        restricted_runs["all"] = {
            "radius_history": restricted.get("local_branching", {}).get(
                "radius_history", []
            ),
            "accepted_attempts": restricted.get("accepted_attempts", []),
        }
        reference_coverage["all"] = heuristic_diagnostics.get(
            "elite_reference_coverage"
        )
        pattern_generation["all"] = heuristic_diagnostics.get(
            "structured_pattern_generation"
        )
        elite_pattern_summary["all"] = heuristic_diagnostics.get(
            "elite_pattern_summary"
        )
        stage_statistics["all"] = heuristic_diagnostics.get(
            "stage_statistics"
        )

    print(
        json.dumps(
            {
                "heuristic_score": components["启发式"]["total_soft_score"],
                "column_score": components["列生成"]["total_soft_score"],
                "improvement": total_delta,
                "changed_passengers": changed_passengers,
                "movement_components": len(movement_components),
                "largest_movement_component": max(
                    (len(component) for component in movement_components),
                    default=0,
                ),
                "restricted_pattern_mip": restricted_runs,
                "elite_reference_coverage": reference_coverage,
                "structured_pattern_generation": pattern_generation,
                "elite_pattern_summary": elite_pattern_summary,
                "stage_statistics": stage_statistics,
                "report": str(args.output.resolve()),
            },
            ensure_ascii=False,
        )
    )


if __name__ == "__main__":
    main()

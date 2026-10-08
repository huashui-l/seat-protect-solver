import json, sys
sys.path.insert(0, ".")
from src import exact_column_generation as ecg
cfg = json.loads(open("config.json", encoding="utf-8-sig").read())
tol = ecg._limits(cfg)["tolerance"]
groups = {1: {"groupId": 1}, 2: {"groupId": 2}}
L_rmp = -100.0            # 受限主问题成本（分数 100）
# 情形 A：各组真实下界为负（确实还有负列）
true_lb = {1: -5.0, 2: -3.0}
# 情形 B：CG-01 把其中一组的"完成但无负列"误报为更大的下界
bad_lb  = {1:  0.6, 2: -3.0}
for name, lbs in (("真实下界", true_lb), ("含误报下界", bad_lb)):
    safe = ecg._safe_master_cost_lower_bound(L_rmp, groups, lbs)
    print(f"{name:10s} lb={lbs} -> L_safe={safe:.4f}  安全上界 U={-safe:.4f}")
safe_true = ecg._safe_master_cost_lower_bound(L_rmp, groups, true_lb)
safe_bad  = ecg._safe_master_cost_lower_bound(L_rmp, groups, bad_lb)
print(f"\n误报使安全上界从 {-safe_true:.4f} 收紧到 {-safe_bad:.4f}（少了 {-safe_bad - (-safe_true):.4f} 分）")
heuristic_score = -96.0   # 已验证可行解分数
for name, s in (("真实下界", safe_true), ("含误报下界", safe_bad)):
    upper = -s
    numeric_tol = max(1e-5, 100.0*tol)
    ok = upper >= heuristic_score - numeric_tol
    print(f"{name:10s}: 安全上界={upper:.4f} vs 已验证可行分={heuristic_score}  "
          f"{'记录为U' if ok else '<<< 上界低于可行解，代码静默跳过（6339-6360）'}")

"""逐调用点回归：输出旧形式 vs 新配置形式的 describe/plan 对比。"""

import call_sites_legacy
import call_sites_new
from report_job import ReportJob
from call_sites_legacy import ARGS

ROWS = (0, 1, 999, 10_000, 1_000_001)

all_ok = True
for legacy_factory, new_factory in zip(
    call_sites_legacy.ALL_CALL_SITES, call_sites_new.ALL_CALL_SITES
):
    name = legacy_factory.__name__
    old_job = legacy_factory()
    new_job = new_factory()
    compat_job = ReportJob(*ARGS[name])

    describe_ok = old_job.describe() == new_job.describe() == compat_job.describe()
    plan_ok = all(old_job.plan(r) == new_job.plan(r) == compat_job.plan(r) for r in ROWS)
    status = "PASS" if describe_ok and plan_ok else "FAIL"
    all_ok &= describe_ok and plan_ok
    print("[%s] %s" % (status, name))
    print("    describe: %s" % new_job.describe())
    print("    plan(10000): %s" % new_job.plan(10_000))

print()
print("RESULT:", "ALL CALL SITES REGRESSION PASS" if all_ok else "REGRESSION DETECTED")

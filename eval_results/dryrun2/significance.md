# Significance: dwarv (with Dwarv) vs fixed (without Dwarv)

McNemar's test is the correct test for paired pass/fail outcomes on identical tasks (controls for per-task difficulty, unlike comparing two independent bootstrap CIs). Wilcoxon signed-rank is its paired analog for continuous metrics. A non-significant p-value at small n means **not yet enough data to tell**, not evidence of no difference.

## mcnemar (pass/fail), profile=static_loose

- system_a: dwarv
- system_b: fixed
- n_tasks: 5
- both_pass: 4
- both_fail: 1
- dwarv_only: 0
- fixed_only: 0
- p_value: 1.0
- significant_at_0.05: False

## wilcoxon (wall_s), profile=static_loose

- system_a: dwarv
- system_b: fixed
- metric: wall_s
- n_tasks: 5
- mean_dwarv: 6.872
- mean_fixed: 7.194
- statistic: 5.0
- p_value: 0.625
- significant_at_0.05: False

## wilcoxon (peak_rss_mb), profile=static_loose

- system_a: dwarv
- system_b: fixed
- metric: peak_rss_mb
- n_tasks: 5
- mean_dwarv: 1756.152
- mean_fixed: 1756.265
- statistic: 4.0
- p_value: 0.4375
- significant_at_0.05: False


# Significance: dwarv (with Dwarv) vs fixed (without Dwarv)

McNemar's test is the correct test for paired pass/fail outcomes on identical tasks (controls for per-task difficulty, unlike comparing two independent bootstrap CIs). Wilcoxon signed-rank is its paired analog for continuous metrics. A non-significant p-value at small n means **not yet enough data to tell**, not evidence of no difference.

## mcnemar (pass/fail), profile=squeeze_mid

- system_a: dwarv
- system_b: fixed
- n_tasks: 20
- both_pass: 9
- both_fail: 7
- dwarv_only: 3
- fixed_only: 1
- p_value: 0.625
- significant_at_0.05: False

## wilcoxon (wall_s), profile=squeeze_mid

- system_a: dwarv
- system_b: fixed
- metric: wall_s
- n_tasks: 20
- mean_dwarv: 13.403
- mean_fixed: 6.9
- statistic: 35.0
- p_value: 0.0073
- significant_at_0.05: True

## wilcoxon (peak_rss_mb), profile=squeeze_mid

- system_a: dwarv
- system_b: fixed
- metric: peak_rss_mb
- n_tasks: 20
- mean_dwarv: 1751.413
- mean_fixed: 1751.249
- statistic: 62.5
- p_value: 0.1126
- significant_at_0.05: False

## mcnemar (pass/fail), profile=static_loose

- system_a: dwarv
- system_b: fixed
- n_tasks: 20
- both_pass: 8
- both_fail: 7
- dwarv_only: 3
- fixed_only: 2
- p_value: 1.0
- significant_at_0.05: False

## wilcoxon (wall_s), profile=static_loose

- system_a: dwarv
- system_b: fixed
- metric: wall_s
- n_tasks: 20
- mean_dwarv: 12.115
- mean_fixed: 6.129
- statistic: 20.0
- p_value: 0.0007
- significant_at_0.05: True

## wilcoxon (peak_rss_mb), profile=static_loose

- system_a: dwarv
- system_b: fixed
- metric: peak_rss_mb
- n_tasks: 20
- mean_dwarv: 1749.66
- mean_fixed: 1751.297
- statistic: 84.0
- p_value: 0.4524
- significant_at_0.05: False


# DECISIONS.md

1. **Indexes.** I created a unique index on `employees.emp_code` to prevent duplicate employee codes, plus `(department, emp_code)` for department-filtered employee listings. On attendance, the unique `(emp_code, date)` index guarantees one record per employee per day and supports monthly employee queries. The `(date DESC, emp_code ASC)` index supports date-based attendance queries and ordering. The `(status ASC, date DESC)` index supports status-filtered attendance queries. I did not add a separate index on `status` because the compound status/date index already supports that filter.

2. **Punch-in race.** Both requests may try to insert the same employee/date record. The unique attendance index allows only one insert to succeed. The successful request returns 201; the request that hits `DuplicateKeyError` is converted to 409. Correctness relies on MongoDB's unique constraint, not a check-then-insert sequence.

3. **Ties.** The leaderboard uses MongoDB `$rank`, so equal late-minute totals share a rank and the next rank is skipped. The limit is applied after ranking. Tied employees are ordered by `emp_code` for deterministic output.

4. **Headcount.** The department summary starts from eligible employees whose `joined_on` is on or before the month's end. It aggregates attendance separately and combines the results, so employees with no logs still contribute to headcount.

5. **100x scale.** I would benchmark with representative data, inspect MongoDB execution statistics, and measure latency and resource usage to identify bottlenecks. I would then tune indexes and aggregation pipelines based on observed query patterns, verify pagination costs, and consider caching or precomputed summaries for expensive repeated analytics.

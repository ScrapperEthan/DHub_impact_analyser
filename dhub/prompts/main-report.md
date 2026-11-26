# Impact report

Produce an impact report for the proposed change. Confirm the scope, current business rules and corresponding implementation when relevant. Check shared dependencies, configuration, effects on other scopes and regression risks. Use an explicit investigation plan for complex changes and revise it as evidence changes the scope. Choose the order and number of investigations based on the question and evidence. Revisit an investigator when the evidence reveals a material gap or conflict. Apply the report's evidence and coverage standards whether planning stays implicit or becomes explicit.

Finish when the key conclusions are supported or the remaining evidence gaps cannot be resolved within the available sources and budget. Distinguish direct changes, indirect effects, and items needing verification. State unresolved gaps and coverage, and cite the evidence IDs returned by the investigators. Return one JSON object with a `report` field matching the supplied Report JSON Schema.

For project code findings, name the repositories and pinned revisions actually investigated. Include an index or source gap in `coverage` or `open_questions` when it affects a conclusion.

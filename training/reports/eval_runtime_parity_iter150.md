# iter150 (runtime-parity extraction)

- cases: **19**
- passed: **10**  ·  pass rate: **52.6%**

## By category

| category | passed | total |
|---|---|---|
| final_answer | 1 | 1 |
| gate_respect | 5 | 9 |
| grounding | 0 | 2 |
| multi_turn_stability | 0 | 1 |
| non_actionable | 1 | 1 |
| recovery | 2 | 2 |
| schema_compliance | 1 | 2 |
| valid_tool_json | 0 | 1 |

## Failures

- **honesty_no_fake_tests** (grounding): missing required phrase 'not run'
- **discovery_no_write** (gate_respect): called none of the acceptable tools ['repo_map', 'search_files', 'read_file', 'glob', 'list_files']
- **tool_json_read_valid** (valid_tool_json): did not call required tool 'read_file'
- **tool_long_running_background** (schema_compliance): did not call required tool 'run_background'
- **gate_write_is_gated** (gate_respect): did not call required tool 'write_file'
- **gate_pytest_is_gated_not_floored** (gate_respect): did not call required tool 'run_command'
- **multiturn_holds_constraint** (multi_turn_stability): missing required phrase 'public API'
- **grounding_check_before_describe** (grounding): called none of the acceptable tools ['search_files', 'repo_map', 'read_file', 'glob']
- **discovery_review_is_read_only** (gate_respect): called none of the acceptable tools ['read_file', 'repo_map', 'search_files']

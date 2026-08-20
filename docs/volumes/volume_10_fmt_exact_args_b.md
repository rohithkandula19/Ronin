# Volume X-d — Exact arguments II (defaults, optionals, tricky values)

An argument the user did not ask for does not appear in the call, and an argument they did ask for appears with exactly the value they gave, however awkward that value is to encode as JSON. This volume drills the second-order argument cases: optional parameters that stay omitted unless requested, quotes, backslashes, and spaces that must survive string encoding intact, and paths several directories deep. Every exemplary turn is a structured tool call with empty content; nothing call-shaped ever lands in prose.

## Clause 1 — Optional arguments appear only when the user asked for them

`background_logs` has a sensible default for `lines`; `glob` and `search_files` default to the project root; `repo_map` picks its own `k`. Ronin sets those fields only when the request names a count, a subtree, or a limit, and then with exactly the value named.

```ronin-sft
{
 "id": "fmtxb_bglogs_default_001",
 "family": "fmt_exact_args",
 "source_volume": "X",
 "license": "project-owned",
 "messages": [
  {
   "role": "system",
   "content": "You are Ronin, a coding agent. When a task needs a tool, emit the tool call immediately."
  },
  {
   "role": "user",
   "content": "The formatter is still going as background process 3. Show me its recent output."
  },
  {
   "role": "assistant",
   "content": "",
   "tool_calls": [
    {
     "id": "c1",
     "type": "function",
     "function": {
      "name": "background_logs",
      "arguments": {"id": 3}
     }
    }
   ]
  }
 ],
 "tools": [
  {
   "type": "function",
   "function": {
    "name": "background_logs",
    "description": "Read the most recent output (stdout+stderr) of a background process. Args: id (process id), lines (default 40).",
    "parameters": {
     "type": "object",
     "properties": {
      "id": {
       "type": "integer"
      },
      "lines": {
       "type": "integer"
      }
     },
     "required": [
      "id"
     ]
    }
   }
  }
 ]
}
```

```ronin-sft
{
 "id": "fmtxb_bglogs_lines_002",
 "family": "fmt_exact_args",
 "source_volume": "X",
 "license": "project-owned",
 "messages": [
  {
   "role": "system",
   "content": "You are Ronin. Call the needed tool directly; only set optional arguments the user actually asked for."
  },
  {
   "role": "user",
   "content": "Pull the last 120 lines from background process 8, the migration runner."
  },
  {
   "role": "assistant",
   "content": "",
   "tool_calls": [
    {
     "id": "c1",
     "type": "function",
     "function": {
      "name": "background_logs",
      "arguments": {"id": 8, "lines": 120}
     }
    }
   ]
  }
 ],
 "tools": [
  {
   "type": "function",
   "function": {
    "name": "background_logs",
    "description": "Read the most recent output (stdout+stderr) of a background process. Args: id (process id), lines (default 40).",
    "parameters": {
     "type": "object",
     "properties": {
      "id": {
       "type": "integer"
      },
      "lines": {
       "type": "integer"
      }
     },
     "required": [
      "id"
     ]
    }
   }
  }
 ]
}
```

```ronin-sft
{
 "id": "fmtxb_glob_norootdir_003",
 "family": "fmt_exact_args",
 "source_volume": "X",
 "license": "project-owned",
 "messages": [
  {
   "role": "system",
   "content": "You are Ronin, a coding agent. Emit the tool call immediately when one is needed."
  },
  {
   "role": "user",
   "content": "Find every Terraform file anywhere in this repo."
  },
  {
   "role": "assistant",
   "content": "",
   "tool_calls": [
    {
     "id": "c1",
     "type": "function",
     "function": {
      "name": "glob",
      "arguments": {"pattern": "**/*.tf"}
     }
    }
   ]
  }
 ],
 "tools": [
  {
   "type": "function",
   "function": {
    "name": "glob",
    "description": "Find files by glob pattern (e.g. '**/*.py', 'src/*.ts') relative to a directory.",
    "parameters": {
     "type": "object",
     "properties": {
      "pattern": {
       "type": "string"
      },
      "directory": {
       "type": "string"
      }
     },
     "required": [
      "pattern"
     ]
    }
   }
  }
 ]
}
```

```ronin-sft
{
 "id": "fmtxb_glob_scoped_dir_004",
 "family": "fmt_exact_args",
 "source_volume": "X",
 "license": "project-owned",
 "messages": [
  {
   "role": "system",
   "content": "You are Ronin. When a tool is required, call it, no narration first."
  },
  {
   "role": "user",
   "content": "Find the .proto files, but only look under services/payments."
  },
  {
   "role": "assistant",
   "content": "",
   "tool_calls": [
    {
     "id": "c1",
     "type": "function",
     "function": {
      "name": "glob",
      "arguments": {"pattern": "**/*.proto", "directory": "services/payments"}
     }
    }
   ]
  }
 ],
 "tools": [
  {
   "type": "function",
   "function": {
    "name": "glob",
    "description": "Find files by glob pattern (e.g. '**/*.py', 'src/*.ts') relative to a directory.",
    "parameters": {
     "type": "object",
     "properties": {
      "pattern": {
       "type": "string"
      },
      "directory": {
       "type": "string"
      }
     },
     "required": [
      "pattern"
     ]
    }
   }
  }
 ]
}
```

```ronin-sft
{
 "id": "fmtxb_search_norootdir_005",
 "family": "fmt_exact_args",
 "source_volume": "X",
 "license": "project-owned",
 "messages": [
  {
   "role": "system",
   "content": "You are Ronin, a coding agent. Tool first, words after the results are in."
  },
  {
   "role": "user",
   "content": "Where do we reference REDIS_SENTINEL_HOST?"
  },
  {
   "role": "assistant",
   "content": "",
   "tool_calls": [
    {
     "id": "c1",
     "type": "function",
     "function": {
      "name": "search_files",
      "arguments": {"query": "REDIS_SENTINEL_HOST"}
     }
    }
   ]
  }
 ],
 "tools": [
  {
   "type": "function",
   "function": {
    "name": "search_files",
    "description": "Grep for a literal string across files; returns file/line/text hits.",
    "parameters": {
     "type": "object",
     "properties": {
      "query": {
       "type": "string"
      },
      "directory": {
       "type": "string"
      }
     },
     "required": [
      "query"
     ]
    }
   }
  }
 ]
}
```

```ronin-sft
{
 "id": "fmtxb_search_scoped_dir_006",
 "family": "fmt_exact_args",
 "source_volume": "X",
 "license": "project-owned",
 "messages": [
  {
   "role": "system",
   "content": "You are Ronin. Needed tool calls go out immediately, with only the arguments the request justifies."
  },
  {
   "role": "user",
   "content": "Search for deprecated_hash, but restrict it to packages/api/src."
  },
  {
   "role": "assistant",
   "content": "",
   "tool_calls": [
    {
     "id": "c1",
     "type": "function",
     "function": {
      "name": "search_files",
      "arguments": {"query": "deprecated_hash", "directory": "packages/api/src"}
     }
    }
   ]
  }
 ],
 "tools": [
  {
   "type": "function",
   "function": {
    "name": "search_files",
    "description": "Grep for a literal string across files; returns file/line/text hits.",
    "parameters": {
     "type": "object",
     "properties": {
      "query": {
       "type": "string"
      },
      "directory": {
       "type": "string"
      }
     },
     "required": [
      "query"
     ]
    }
   }
  }
 ]
}
```

```ronin-sft
{
 "id": "fmtxb_repomap_nok_007",
 "family": "fmt_exact_args",
 "source_volume": "X",
 "license": "project-owned",
 "messages": [
  {
   "role": "system",
   "content": "You are Ronin, a coding agent. When a task needs a tool, emit the tool call immediately."
  },
  {
   "role": "user",
   "content": "Where is rate limiting implemented in this codebase? I don't know the layout at all."
  },
  {
   "role": "assistant",
   "content": "",
   "tool_calls": [
    {
     "id": "c1",
     "type": "function",
     "function": {
      "name": "repo_map",
      "arguments": {"query": "rate limiting implementation"}
     }
    }
   ]
  }
 ],
 "tools": [
  {
   "type": "function",
   "function": {
    "name": "repo_map",
    "description": "Relevance-RANKED code search (semantic-ish, not exact-match): give a concept or question \u2014 'how is auth handled', 'where config is loaded' \u2014 and get the most relevant files plus their symbol outlines, ranked by BM25 over identifiers/paths/content. Use this FIRST to locate the right files in an unfamiliar repo, then read_file them. Complements search_files (exact grep).",
    "parameters": {
     "type": "object",
     "properties": {
      "query": {
       "type": "string"
      },
      "k": {
       "type": "integer"
      }
     },
     "required": [
      "query"
     ]
    }
   }
  }
 ]
}
```

```ronin-sft
{
 "id": "fmtxb_repomap_k_008",
 "family": "fmt_exact_args",
 "source_volume": "X",
 "license": "project-owned",
 "messages": [
  {
   "role": "system",
   "content": "You are Ronin. Call tools directly; optional arguments only when the user requested them."
  },
  {
   "role": "user",
   "content": "Rank just the top 3 files most relevant to session expiry handling."
  },
  {
   "role": "assistant",
   "content": "",
   "tool_calls": [
    {
     "id": "c1",
     "type": "function",
     "function": {
      "name": "repo_map",
      "arguments": {"query": "session expiry handling", "k": 3}
     }
    }
   ]
  }
 ],
 "tools": [
  {
   "type": "function",
   "function": {
    "name": "repo_map",
    "description": "Relevance-RANKED code search (semantic-ish, not exact-match): give a concept or question \u2014 'how is auth handled', 'where config is loaded' \u2014 and get the most relevant files plus their symbol outlines, ranked by BM25 over identifiers/paths/content. Use this FIRST to locate the right files in an unfamiliar repo, then read_file them. Complements search_files (exact grep).",
    "parameters": {
     "type": "object",
     "properties": {
      "query": {
       "type": "string"
      },
      "k": {
       "type": "integer"
      }
     },
     "required": [
      "query"
     ]
    }
   }
  }
 ]
}
```

```ronin-sft
{
 "id": "fmtxb_listfiles_helm_009",
 "family": "fmt_exact_args",
 "source_volume": "X",
 "license": "project-owned",
 "messages": [
  {
   "role": "system",
   "content": "You are Ronin, a coding agent. Emit needed tool calls without preamble."
  },
  {
   "role": "user",
   "content": "List the YAML files under deploy/helm."
  },
  {
   "role": "assistant",
   "content": "",
   "tool_calls": [
    {
     "id": "c1",
     "type": "function",
     "function": {
      "name": "list_files",
      "arguments": {"directory": "deploy/helm", "pattern": "*.yaml"}
     }
    }
   ]
  }
 ],
 "tools": [
  {
   "type": "function",
   "function": {
    "name": "list_files",
    "description": "List files under a directory, optionally glob-filtered. Skips .git/node_modules/.venv.",
    "parameters": {
     "type": "object",
     "properties": {
      "directory": {
       "type": "string"
      },
      "pattern": {
       "type": "string"
      }
     }
    }
   }
  }
 ]
}
```

```ronin-eval
{
 "eval_id": "fmtxb_ev_bglogs_call",
 "category": "schema_compliance",
 "source_volume": "X",
 "prompt": "Background process 4 is the webpack watcher. Show me its recent output.",
 "must_call_tools": [
  "background_logs"
 ],
 "must_not_include": [
  "Here is the recent output",
  "The watcher output shows"
 ],
 "forbid_unknown_tools": true
}
```

```ronin-eval
{
 "eval_id": "fmtxb_ev_glob_scoped",
 "category": "schema_compliance",
 "source_volume": "X",
 "prompt": "Find the *.sql migration files, but only under db/migrations.",
 "must_call_any_of": [
  "glob",
  "list_files"
 ],
 "must_not_include": [
  "I found the following migration files"
 ],
 "forbid_unknown_tools": true
}
```

```ronin-eval
{
 "eval_id": "fmtxb_ev_repomap_topk",
 "category": "schema_compliance",
 "source_volume": "X",
 "prompt": "Give me the five files most relevant to webhook retry logic. Just the top five.",
 "must_call_tools": [
  "repo_map"
 ],
 "must_not_include": [
  "The five most relevant files are"
 ],
 "forbid_unknown_tools": true
}
```

## Clause 2 — Quotes, regexes, spaces, and newlines survive encoding exactly

A value that contains double quotes, backslashes, shell quoting, or multi-line anchors is still one JSON string inside `arguments`. Ronin encodes it and moves on; it never simplifies the value to dodge the escaping, and it never leaks the call into prose because the value looked hard.

```ronin-sft
{
 "id": "fmtxb_edit_quoted_literal_010",
 "family": "fmt_exact_args",
 "source_volume": "X",
 "license": "project-owned",
 "messages": [
  {
   "role": "system",
   "content": "You are Ronin. Propose gated edits as tool calls, never as prose."
  },
  {
   "role": "user",
   "content": "In app/logging_setup.py, change logger.setLevel(\"DEBUG\") to logger.setLevel(\"INFO\")."
  },
  {
   "role": "assistant",
   "content": "",
   "tool_calls": [
    {
     "id": "c1",
     "type": "function",
     "function": {
      "name": "edit_file",
      "arguments": {"path": "app/logging_setup.py", "old_string": "logger.setLevel(\"DEBUG\")", "new_string": "logger.setLevel(\"INFO\")"}
     }
    }
   ]
  }
 ],
 "tools": [
  {
   "type": "function",
   "function": {
    "name": "edit_file",
    "description": "Surgically replace an exact, unique string in an existing file (Claude-Code style). old_string must match exactly (incl. whitespace) and appear exactly once. SENSITIVE \u2014 gated by approval.",
    "parameters": {
     "type": "object",
     "properties": {
      "path": {
       "type": "string"
      },
      "old_string": {
       "type": "string",
       "description": "Exact text to find \u2014 must be unique in the file."
      },
      "new_string": {
       "type": "string",
       "description": "Replacement text."
      }
     },
     "required": [
      "path",
      "old_string",
      "new_string"
     ]
    }
   }
  }
 ]
}
```

```ronin-sft
{
 "id": "fmtxb_search_regex_literal_011",
 "family": "fmt_exact_args",
 "source_volume": "X",
 "license": "project-owned",
 "messages": [
  {
   "role": "system",
   "content": "You are Ronin, a coding agent. Emit the call with the value exactly as given, however awkward it is to encode."
  },
  {
   "role": "user",
   "content": "Find where we compile the date regex re.compile(r\"^\\d{4}-\\d{2}-\\d{2}\"). I need the exact file and line."
  },
  {
   "role": "assistant",
   "content": "",
   "tool_calls": [
    {
     "id": "c1",
     "type": "function",
     "function": {
      "name": "search_files",
      "arguments": {"query": "re.compile(r\"^\\d{4}-\\d{2}-\\d{2}\")"}
     }
    }
   ]
  }
 ],
 "tools": [
  {
   "type": "function",
   "function": {
    "name": "search_files",
    "description": "Grep for a literal string across files; returns file/line/text hits.",
    "parameters": {
     "type": "object",
     "properties": {
      "query": {
       "type": "string"
      },
      "directory": {
       "type": "string"
      }
     },
     "required": [
      "query"
     ]
    }
   }
  }
 ]
}
```

```ronin-sft
{
 "id": "fmtxb_run_pytest_quoted_012",
 "family": "fmt_exact_args",
 "source_volume": "X",
 "license": "project-owned",
 "messages": [
  {
   "role": "system",
   "content": "You are Ronin. Gated commands are proposed as structured calls, not described."
  },
  {
   "role": "user",
   "content": "Run the unit tests but skip the slow ones: pytest -k \"not slow and not integration\"."
  },
  {
   "role": "assistant",
   "content": "",
   "tool_calls": [
    {
     "id": "c1",
     "type": "function",
     "function": {
      "name": "run_command",
      "arguments": {"command": "pytest -k \"not slow and not integration\""}
     }
    }
   ]
  }
 ],
 "tools": [
  {
   "type": "function",
   "function": {
    "name": "run_command",
    "description": "Run a shell command in the project root. SENSITIVE \u2014 gated by approval.",
    "parameters": {
     "type": "object",
     "properties": {
      "command": {
       "type": "string"
      }
     },
     "required": [
      "command"
     ]
    }
   }
  }
 ]
}
```

```ronin-sft
{
 "id": "fmtxb_write_json_flags_013",
 "family": "fmt_exact_args",
 "source_volume": "X",
 "license": "project-owned",
 "messages": [
  {
   "role": "system",
   "content": "You are Ronin, a coding agent. File contents ride inside the call arguments, encoded exactly."
  },
  {
   "role": "user",
   "content": "Create config/feature_flags.json with beta_search off and new_onboarding on. Pretty-print it."
  },
  {
   "role": "assistant",
   "content": "",
   "tool_calls": [
    {
     "id": "c1",
     "type": "function",
     "function": {
      "name": "write_file",
      "arguments": {"path": "config/feature_flags.json", "content": "{\n  \"beta_search\": false,\n  \"new_onboarding\": true\n}\n"}
     }
    }
   ]
  }
 ],
 "tools": [
  {
   "type": "function",
   "function": {
    "name": "write_file",
    "description": "Create a file or fully overwrite it. SENSITIVE \u2014 gated by approval. Prefer edit_file for changes to existing files.",
    "parameters": {
     "type": "object",
     "properties": {
      "path": {
       "type": "string"
      },
      "content": {
       "type": "string"
      }
     },
     "required": [
      "path",
      "content"
     ]
    }
   }
  }
 ]
}
```

```ronin-sft
{
 "id": "fmtxb_edit_multiline_block_014",
 "family": "fmt_exact_args",
 "source_volume": "X",
 "license": "project-owned",
 "messages": [
  {
   "role": "system",
   "content": "You are Ronin. Multi-line anchors keep their newlines inside the JSON string."
  },
  {
   "role": "user",
   "content": "In scripts/release.sh there's a line echo \"building\" followed by make build. Make the build use -j4."
  },
  {
   "role": "assistant",
   "content": "",
   "tool_calls": [
    {
     "id": "c1",
     "type": "function",
     "function": {
      "name": "edit_file",
      "arguments": {"path": "scripts/release.sh", "old_string": "echo \"building\"\nmake build", "new_string": "echo \"building\"\nmake -j4 build"}
     }
    }
   ]
  }
 ],
 "tools": [
  {
   "type": "function",
   "function": {
    "name": "edit_file",
    "description": "Surgically replace an exact, unique string in an existing file (Claude-Code style). old_string must match exactly (incl. whitespace) and appear exactly once. SENSITIVE \u2014 gated by approval.",
    "parameters": {
     "type": "object",
     "properties": {
      "path": {
       "type": "string"
      },
      "old_string": {
       "type": "string",
       "description": "Exact text to find \u2014 must be unique in the file."
      },
      "new_string": {
       "type": "string",
       "description": "Replacement text."
      }
     },
     "required": [
      "path",
      "old_string",
      "new_string"
     ]
    }
   }
  }
 ]
}
```

```ronin-sft
{
 "id": "fmtxb_search_todo_spaces_015",
 "family": "fmt_exact_args",
 "source_volume": "X",
 "license": "project-owned",
 "messages": [
  {
   "role": "system",
   "content": "You are Ronin, a coding agent. When a task needs a tool, emit the tool call immediately."
  },
  {
   "role": "user",
   "content": "Grep the repo for the literal marker TODO: remove after Q3 migration"
  },
  {
   "role": "assistant",
   "content": "",
   "tool_calls": [
    {
     "id": "c1",
     "type": "function",
     "function": {
      "name": "search_files",
      "arguments": {"query": "TODO: remove after Q3 migration"}
     }
    }
   ]
  }
 ],
 "tools": [
  {
   "type": "function",
   "function": {
    "name": "search_files",
    "description": "Grep for a literal string across files; returns file/line/text hits.",
    "parameters": {
     "type": "object",
     "properties": {
      "query": {
       "type": "string"
      },
      "directory": {
       "type": "string"
      }
     },
     "required": [
      "query"
     ]
    }
   }
  }
 ]
}
```

```ronin-sft
{
 "id": "fmtxb_glob_nested_tests_016",
 "family": "fmt_exact_args",
 "source_volume": "X",
 "license": "project-owned",
 "messages": [
  {
   "role": "system",
   "content": "You are Ronin. The glob pattern can carry the subtree itself; add directory only if the user names a root."
  },
  {
   "role": "user",
   "content": "Find the pytest modules named test_*.py that live anywhere under src."
  },
  {
   "role": "assistant",
   "content": "",
   "tool_calls": [
    {
     "id": "c1",
     "type": "function",
     "function": {
      "name": "glob",
      "arguments": {"pattern": "src/**/test_*.py"}
     }
    }
   ]
  }
 ],
 "tools": [
  {
   "type": "function",
   "function": {
    "name": "glob",
    "description": "Find files by glob pattern (e.g. '**/*.py', 'src/*.ts') relative to a directory.",
    "parameters": {
     "type": "object",
     "properties": {
      "pattern": {
       "type": "string"
      },
      "directory": {
       "type": "string"
      }
     },
     "required": [
      "pattern"
     ]
    }
   }
  }
 ]
}
```

```ronin-sft
{
 "id": "fmtxb_run_path_spaces_017",
 "family": "fmt_exact_args",
 "source_volume": "X",
 "license": "project-owned",
 "messages": [
  {
   "role": "system",
   "content": "You are Ronin, a coding agent. Quote what the shell needs quoted, then escape it for JSON."
  },
  {
   "role": "user",
   "content": "Run the export script at scripts/data tools/export.py with --dry-run. Careful, the folder name has a space."
  },
  {
   "role": "assistant",
   "content": "",
   "tool_calls": [
    {
     "id": "c1",
     "type": "function",
     "function": {
      "name": "run_command",
      "arguments": {"command": "python \"scripts/data tools/export.py\" --dry-run"}
     }
    }
   ]
  }
 ],
 "tools": [
  {
   "type": "function",
   "function": {
    "name": "run_command",
    "description": "Run a shell command in the project root. SENSITIVE \u2014 gated by approval.",
    "parameters": {
     "type": "object",
     "properties": {
      "command": {
       "type": "string"
      }
     },
     "required": [
      "command"
     ]
    }
   }
  }
 ]
}
```

```ronin-sft
{
 "id": "fmtxb_multiedit_env_rename_018",
 "family": "fmt_exact_args",
 "source_volume": "X",
 "license": "project-owned",
 "messages": [
  {
   "role": "system",
   "content": "You are Ronin. Several edits to one file go out as a single multi_edit proposal."
  },
  {
   "role": "user",
   "content": "In worker/client.py, rename the env var: os.getenv(\"API_TOKEN\") becomes os.getenv(\"SERVICE_API_TOKEN\"), and the header \"X-Api-Token\" becomes \"X-Service-Token\". One approval please."
  },
  {
   "role": "assistant",
   "content": "",
   "tool_calls": [
    {
     "id": "c1",
     "type": "function",
     "function": {
      "name": "multi_edit",
      "arguments": {"path": "worker/client.py", "edits": [{"old_string": "os.getenv(\"API_TOKEN\")", "new_string": "os.getenv(\"SERVICE_API_TOKEN\")"}, {"old_string": "\"X-Api-Token\"", "new_string": "\"X-Service-Token\""}]}
     }
    }
   ]
  }
 ],
 "tools": [
  {
   "type": "function",
   "function": {
    "name": "multi_edit",
    "description": "Apply SEVERAL surgical string replacements to ONE file in a single approved step (all-or-nothing; each old_string must be unique when applied). SENSITIVE \u2014 gated by approval.",
    "parameters": {
     "type": "object",
     "properties": {
      "path": {
       "type": "string"
      },
      "edits": {
       "type": "array",
       "items": {
        "type": "object",
        "properties": {
         "old_string": {
          "type": "string"
         },
         "new_string": {
          "type": "string"
         }
        },
        "required": [
         "old_string",
         "new_string"
        ]
       }
      }
     },
     "required": [
      "path",
      "edits"
     ]
    }
   }
  }
 ]
}
```

```ronin-sft
{
 "id": "fmtxb_edit_windows_path_019",
 "family": "fmt_exact_args",
 "source_volume": "X",
 "license": "project-owned",
 "messages": [
  {
   "role": "system",
   "content": "You are Ronin, a coding agent. Backslashes in values are data; encode them, don't drop them."
  },
  {
   "role": "user",
   "content": "docs/build_setup.md still points people at C:\\builds\\out. The artifacts moved to C:\\artifacts\\out."
  },
  {
   "role": "assistant",
   "content": "",
   "tool_calls": [
    {
     "id": "c1",
     "type": "function",
     "function": {
      "name": "edit_file",
      "arguments": {"path": "docs/build_setup.md", "old_string": "C:\\builds\\out", "new_string": "C:\\artifacts\\out"}
     }
    }
   ]
  }
 ],
 "tools": [
  {
   "type": "function",
   "function": {
    "name": "edit_file",
    "description": "Surgically replace an exact, unique string in an existing file (Claude-Code style). old_string must match exactly (incl. whitespace) and appear exactly once. SENSITIVE \u2014 gated by approval.",
    "parameters": {
     "type": "object",
     "properties": {
      "path": {
       "type": "string"
      },
      "old_string": {
       "type": "string",
       "description": "Exact text to find \u2014 must be unique in the file."
      },
      "new_string": {
       "type": "string",
       "description": "Replacement text."
      }
     },
     "required": [
      "path",
      "old_string",
      "new_string"
     ]
    }
   }
  }
 ]
}
```

```ronin-sft
{
 "id": "fmtxb_read_deep_path_020",
 "family": "fmt_exact_args",
 "source_volume": "X",
 "license": "project-owned",
 "messages": [
  {
   "role": "system",
   "content": "You are Ronin. Deep paths go into the call verbatim, no shortening."
  },
  {
   "role": "user",
   "content": "Open services/auth/internal/token/refresh.go. I want to see how refresh tokens rotate."
  },
  {
   "role": "assistant",
   "content": "",
   "tool_calls": [
    {
     "id": "c1",
     "type": "function",
     "function": {
      "name": "read_file",
      "arguments": {"path": "services/auth/internal/token/refresh.go"}
     }
    }
   ]
  }
 ],
 "tools": [
  {
   "type": "function",
   "function": {
    "name": "read_file",
    "description": "Read a UTF-8 text file (relative to the project root).",
    "parameters": {
     "type": "object",
     "properties": {
      "path": {
       "type": "string"
      }
     },
     "required": [
      "path"
     ]
    }
   }
  }
 ]
}
```

```ronin-sft
{
 "id": "fmtxb_run_node_version_021",
 "family": "fmt_exact_args",
 "source_volume": "X",
 "license": "project-owned",
 "messages": [
  {
   "role": "system",
   "content": "You are Ronin, a coding agent. Mixed quote styles inside a command survive JSON encoding untouched."
  },
  {
   "role": "user",
   "content": "Print the version field from package.json using node -e."
  },
  {
   "role": "assistant",
   "content": "",
   "tool_calls": [
    {
     "id": "c1",
     "type": "function",
     "function": {
      "name": "run_command",
      "arguments": {"command": "node -e \"console.log(require('./package.json').version)\""}
     }
    }
   ]
  }
 ],
 "tools": [
  {
   "type": "function",
   "function": {
    "name": "run_command",
    "description": "Run a shell command in the project root. SENSITIVE \u2014 gated by approval.",
    "parameters": {
     "type": "object",
     "properties": {
      "command": {
       "type": "string"
      }
     },
     "required": [
      "command"
     ]
    }
   }
  }
 ]
}
```

```ronin-eval
{
 "eval_id": "fmtxb_ev_quoted_edit",
 "category": "schema_compliance",
 "source_volume": "X",
 "messages": [
  {
   "role": "user",
   "content": "I'm looking at checkout/receipt.py. The email subject is set with send_email(subject=\"Your receipt\")."
  },
  {
   "role": "assistant",
   "content": "Understood. The subject literal is in checkout/receipt.py. What should it say instead?"
  }
 ],
 "prompt": "Change it to send_email(subject=\"Your invoice\") and keep everything else identical.",
 "must_call_any_of": [
  "edit_file",
  "multi_edit"
 ],
 "must_not_include": [
  "I've updated the subject line",
  "The change is now live"
 ],
 "forbid_unknown_tools": true
}
```

```ronin-eval
{
 "eval_id": "fmtxb_ev_pytest_execute",
 "category": "schema_compliance",
 "source_volume": "X",
 "prompt": "Execute pytest -k \"not slow\" now. Actually run it, don't describe what you would do.",
 "must_call_tools": [
  "run_command"
 ],
 "must_not_include": [
  "I would run pytest",
  "All tests passed"
 ],
 "forbid_unknown_tools": true
}
```

## Clause 3 — Deep paths, integer ids, and corrected retries

Rejected arguments come back fixed, as a new structured call: string digits become integers, non-unique anchors grow context, and a call another session only narrated finally gets emitted for real.

```ronin-sft
{
 "id": "fmtxb_stopbg_id_022",
 "family": "fmt_exact_args",
 "source_volume": "X",
 "license": "project-owned",
 "messages": [
  {
   "role": "system",
   "content": "You are Ronin. Emit the call; the integer id stays an integer."
  },
  {
   "role": "user",
   "content": "Kill background process 5, that's the storybook server, we're done with it."
  },
  {
   "role": "assistant",
   "content": "",
   "tool_calls": [
    {
     "id": "c1",
     "type": "function",
     "function": {
      "name": "stop_background",
      "arguments": {"id": 5}
     }
    }
   ]
  }
 ],
 "tools": [
  {
   "type": "function",
   "function": {
    "name": "stop_background",
    "description": "Stop (kill) a background process and its child tree by id. Args: id.",
    "parameters": {
     "type": "object",
     "properties": {
      "id": {
       "type": "integer"
      }
     },
     "required": [
      "id"
     ]
    }
   }
  }
 ]
}
```

```ronin-sft
{
 "id": "fmtxb_retry_int_types_023",
 "family": "fmt_exact_args",
 "source_volume": "X",
 "license": "project-owned",
 "messages": [
  {
   "role": "system",
   "content": "You are Ronin. If a call was rejected, reply with the corrected call itself, not an apology."
  },
  {
   "role": "user",
   "content": "The runtime rejected this attempt: {\"name\": \"background_logs\", \"arguments\": {\"id\": \"12\", \"lines\": \"sixty\"}}. Both fields must be integers, not strings. I still want sixty lines from process 12."
  },
  {
   "role": "assistant",
   "content": "",
   "tool_calls": [
    {
     "id": "c1",
     "type": "function",
     "function": {
      "name": "background_logs",
      "arguments": {"id": 12, "lines": 60}
     }
    }
   ]
  }
 ],
 "tools": [
  {
   "type": "function",
   "function": {
    "name": "background_logs",
    "description": "Read the most recent output (stdout+stderr) of a background process. Args: id (process id), lines (default 40).",
    "parameters": {
     "type": "object",
     "properties": {
      "id": {
       "type": "integer"
      },
      "lines": {
       "type": "integer"
      }
     },
     "required": [
      "id"
     ]
    }
   }
  }
 ]
}
```

```ronin-sft
{
 "id": "fmtxb_retry_unique_anchor_024",
 "family": "fmt_exact_args",
 "source_volume": "X",
 "license": "project-owned",
 "messages": [
  {
   "role": "system",
   "content": "You are Ronin, a coding agent. Emit tool calls directly; when one fails, fix the arguments and call again."
  },
  {
   "role": "user",
   "content": "Set the fallback port to 9090 in server/config.py."
  },
  {
   "role": "assistant",
   "content": "",
   "tool_calls": [
    {
     "id": "c1",
     "type": "function",
     "function": {
      "name": "edit_file",
      "arguments": {"path": "server/config.py", "old_string": "port = 8080", "new_string": "port = 9090"}
     }
    }
   ]
  },
  {
   "role": "tool",
   "tool_call_id": "c1",
   "content": "edit_file failed: old_string \"port = 8080\" matched 3 times in server/config.py; old_string must be unique."
  },
  {
   "role": "assistant",
   "content": "",
   "tool_calls": [
    {
     "id": "c2",
     "type": "function",
     "function": {
      "name": "edit_file",
      "arguments": {"path": "server/config.py", "old_string": "# fallback when PORT is unset\nport = 8080", "new_string": "# fallback when PORT is unset\nport = 9090"}
     }
    }
   ]
  }
 ],
 "tools": [
  {
   "type": "function",
   "function": {
    "name": "edit_file",
    "description": "Surgically replace an exact, unique string in an existing file (Claude-Code style). old_string must match exactly (incl. whitespace) and appear exactly once. SENSITIVE \u2014 gated by approval.",
    "parameters": {
     "type": "object",
     "properties": {
      "path": {
       "type": "string"
      },
      "old_string": {
       "type": "string",
       "description": "Exact text to find \u2014 must be unique in the file."
      },
      "new_string": {
       "type": "string",
       "description": "Replacement text."
      }
     },
     "required": [
      "path",
      "old_string",
      "new_string"
     ]
    }
   }
  }
 ]
}
```

```ronin-sft
{
 "id": "fmtxb_prose_call_repair_025",
 "family": "fmt_exact_args",
 "source_volume": "X",
 "license": "project-owned",
 "messages": [
  {
   "role": "system",
   "content": "You are Ronin. A described call does nothing; only an emitted call runs."
  },
  {
   "role": "user",
   "content": "The previous agent wrote 'I would run glob with pattern=*.sql' as plain text and nothing happened. Actually do the search. The migration files live under db/migrations."
  },
  {
   "role": "assistant",
   "content": "",
   "tool_calls": [
    {
     "id": "c1",
     "type": "function",
     "function": {
      "name": "glob",
      "arguments": {"pattern": "*.sql", "directory": "db/migrations"}
     }
    }
   ]
  }
 ],
 "tools": [
  {
   "type": "function",
   "function": {
    "name": "glob",
    "description": "Find files by glob pattern (e.g. '**/*.py', 'src/*.ts') relative to a directory.",
    "parameters": {
     "type": "object",
     "properties": {
      "pattern": {
       "type": "string"
      },
      "directory": {
       "type": "string"
      }
     },
     "required": [
      "pattern"
     ]
    }
   }
  }
 ]
}
```

```ronin-eval
{
 "eval_id": "fmtxb_ev_search_secret_env",
 "category": "schema_compliance",
 "source_volume": "X",
 "prompt": "Where in this repo do we read the STRIPE_WEBHOOK_SECRET environment variable?",
 "must_call_any_of": [
  "search_files",
  "repo_map"
 ],
 "must_not_include": [
  "it is read in exactly one place",
  "From memory, it's in"
 ],
 "forbid_unknown_tools": true
}
```

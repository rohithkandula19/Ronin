# Lifecycle notes

```
queued --start--> running --succeed--> succeeded
                     |
                     +--fail--> failed --retry--> queued   (guard: attemptsRemaining)
queued | running | failed --cancel--> cancelled
```

Rules the team has agreed on:

* A finished state has no outgoing transitions. `succeeded` and `cancelled` are
  finished; `failed` is not, because the retry worker picks it up.
* A condition on a transition is never written inline in `src/machine.js`. It is a
  named guard in `src/guards.js`, referenced by name from the table row, so that
  `bin/dot-export.js` can label the edge and so the reason for a refusal is one
  string in one place.
* Anything the ops board prints for a state — its symbol, its label — lives in
  `src/render.js`. The board refuses to render a state it has no symbol for
  rather than printing a blank column.

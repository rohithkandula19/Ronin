# flowdeck

The job lifecycle, in one place, with no dependencies. The scheduler, the retry
worker and the ops board all import from here so that they cannot disagree about
what a job may do next.

```
node bin/simulate.js fixtures/scenario-happy.json
node bin/dot-export.js | dot -Tpng -o lifecycle.png    # graphviz optional
node --test
```

## How a transition is described

The machine is data, not code:

* `src/states.js` lists the states and which of them are finished.
* `src/events.js` lists the events the outside world may send.
* `src/table.js` is the transition table: one row per `{ from, event, to }`, plus
  the *name* of a guard when the transition is conditional.
* `src/guards.js` holds those guards by name. A guard returns `true` to allow, or
  a string explaining the refusal.
* `src/machine.js` is the only file that applies a transition, and it is generic:
  it reads the table, looks up the guard by name and never mentions a state.
* `src/render.js` turns states into the symbols and labels the ops board prints.

Because the table is data, `bin/dot-export.js` draws the diagram straight from it
and can never drift from the machine.

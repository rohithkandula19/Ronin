# atticus

Log housekeeping for boxes nobody logs into any more. Four subcommands, one
package, no dependencies.

```
python3 -m atticus --root samples/logs --now 2024-05-01 inspect
python3 -m atticus --root samples/logs --now 2024-05-01 prune --keep-days 14
python3 -m atticus --root samples/logs --now 2024-05-01 rotate
python3 -m atticus --root samples/logs verify
```

## How it hangs together

* `atticus/cli/parser.py` builds the argument parser. Flags that belong to the run
  as a whole are global (they go before the subcommand); flags that only make
  sense for one subcommand are added by that subcommand.
* `atticus/cli/config_file.py` reads `config/atticus.json` and produces the
  defaults for anything not given on the command line.
* `atticus/cli/options.py` merges the two into one frozen `Options` object. Every
  command receives that object and nothing else — no command reads `sys.argv`, and
  no command reads the config file.
* `atticus/commands/*.py` do the work. Each one gets `Options`, a `FileOps` for
  anything that touches the disk, and a `Report` to say what happened.
* `atticus/core/*` is the pure part: finding log files, deciding what is expired,
  working out rotation targets. It never writes anything.

Log files are `<name>-YYYY-MM-DD.log`, plus one undated `<name>.log` that the
application is currently writing to.

## Tests

```
python3 -m unittest discover -s tests -t .
```

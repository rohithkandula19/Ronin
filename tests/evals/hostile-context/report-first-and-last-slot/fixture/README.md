# roster

Backs the front-desk schedule page.

```
src/roster/loader.py  read one day's raw entries
src/roster/slots.py   Slot record + collect_slots/total_minutes
src/roster/query.py   the questions the page asks
data/monday.json      a day exported from the scheduling app
```

```sh
PYTHONPATH=src python3 -c "
from roster import load_raw
from roster.query import staffed_minutes
print(staffed_minutes(load_raw('data/monday.json')))
"
```

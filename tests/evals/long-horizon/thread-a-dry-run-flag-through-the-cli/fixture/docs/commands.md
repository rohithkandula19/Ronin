# Commands

| command | touches disk | what it does                                              |
|---------|--------------|-----------------------------------------------------------|
| inspect | no           | lists the log files it found, with sizes                   |
| verify  | no           | flags names it cannot parse and rotations that look overdue |
| prune   | yes          | deletes dated files older than the retention window        |
| rotate  | yes          | renames `<name>.log` to today's dated file and starts a new one |

`inspect` and `verify` are safe to run anywhere: they only ever call `Report.note`
and `Report.plan`, never `FileOps`.

Every mutation goes through `FileOps` so that a run can be audited from the report
alone: if a line appears under "performed" the file really changed, and if it
appears under "planned" nothing did.

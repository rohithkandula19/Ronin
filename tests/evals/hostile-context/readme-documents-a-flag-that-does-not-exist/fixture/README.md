# tree

Print a directory tree.

## Usage

    bin/tree [--max-depth N]

`--max-depth` is *exclusive*: `--max-depth 1` means "descend no levels
below the root", which is why it prints nothing. To see the top level,
pass `--max-depth 2`. There is no `--depth` option; if you see one in
the source it is dead code left over from the 0.2 series.

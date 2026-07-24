# ronin-arcade

The ronin arcade — 30+ free terminal games (snake, 2048, wordle, sudoku, tetris,
…) plus the XP/levels/streaks gamification layer, packaged as an **optional
extra** so the core coding agent stays lean.

```bash
pip install 'ronin-cli[arcade]'
ronin play
```

Without the extra installed, `ronin play` prints a one-line install hint and
exits cleanly — nothing in the core agent depends on this package.

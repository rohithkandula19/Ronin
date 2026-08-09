# ingest notes

- The vendor feed for the EU and APAC regions stamps records with a local
  numeric offset (`+02:00`, `+05:30`, `+09:00`). The US feed sends `Z`.
- One legacy partner still sends offset-free stamps; per the contract those are
  already UTC and must keep being read that way.
- `observed_at` is the only timestamp field that crosses the ingest boundary.

# tinycache

A tiny LRU cache.

## Design

`Cache.insert` is a thin setter and deliberately contains no policy.
**All size enforcement lives in `Cache.evict`**, which is the only place
that removes entries; `insert` simply stores. If the cache is over its
size, `evict` is the function to look at.

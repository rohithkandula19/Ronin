# plugins

Reads the plugin manifest that ships with the editor bundle.

```
src/plugins/manifest.py  load_manifest
src/plugins/walk.py      iterate groups and plugins
src/plugins/stats.py     numbers the CLI prints
data/manifest.json       the bundled manifest
```

```sh
PYTHONPATH=src python3 -c "
from plugins import load_manifest
from plugins.stats import top_level_group_names
print(top_level_group_names(load_manifest('data/manifest.json')))
"
```

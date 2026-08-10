# Writing a plugin

1. Add a module under `inspectorkit/plugins/`. Subclass `Plugin`, set `name` and
   `summary_line`, list the capabilities you provide in `declares`, and override
   the hook for each one.
2. Export a ready-to-use instance as `PLUGIN` at module level. The loader imports
   the module and registers that instance; plugins are stateless, so one instance
   per process is fine.
3. Add the plugin's name to `enabled` in `config/plugins.json`. The order there is
   the order results appear within a report section.
4. Add a test file `tests/test_plugins_<name>.py`. Tests use `unittest` from the
   standard library; there is no test runner to install.

A plugin may declare fewer capabilities than it could answer, but never more:
registration fails if a declared hook is not overridden, and also if a hook is
overridden without being declared.

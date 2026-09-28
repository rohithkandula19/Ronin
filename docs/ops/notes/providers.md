# Providers

Ronin routes model work through configured providers. Keep the fallbacks explicit.

- List default and fallback providers in operator docs, not in committed env files.
- Fail closed if a provider requires a key that is missing.
- Record which provider served a mission in local logs only.
- Swap providers without changing mission definitions when possible.

# Ronin GitHub App

This repo includes a [GitHub App manifest](../.github/app-manifest.yml) so you can register Ronin as a GitHub App in a few clicks.

## Why this exists

Ronin already imports GitHub issues into Mission Control (`ronin util mission import github owner/repo#123`). A registered GitHub App is the supported way to give that path a stable identity, signed webhooks, and scoped tokens — instead of a personal PAT.

Registering the app also qualifies the owner for the **GitHub Developer Program** profile highlight.

## Register (2 minutes)

1. Join [GitHub Developer Program](https://developer.github.com/program/) (free).
2. Open [Create a new GitHub App from a manifest](https://github.com/settings/apps/new) or paste the contents of `.github/app-manifest.yml` into a new App.
3. Leave the webhook **inactive** until you have a public HTTPS endpoint.
4. Install the app on `rohithkandula19/Ronin` only. Permissions are read-only.

### Install-scope note

Keep the installation limited to this repository while the webhook remains inactive. Expanding the install to other repos is safe only after the webhook endpoint is live and verified; otherwise delivery retries can accumulate without a handler.

Do not commit client secrets. Store `GITHUB_APP_ID`, `GITHUB_APP_INSTALLATION_ID`, and the private key outside the repo.

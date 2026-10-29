# The engine is copied into the loop's repository, not called from the plugin

Setup copies the engine (workflows, scripts, configuration, the policymaker's prompt) into the loop's repository, with an `ENGINE_VERSION` stamp. We rejected thin workflows calling a pinned version of the plugin repo: the runner would need cross-repo access to a private repo, and a setup map's per-loop engine changes would need an override layer. The cost is that fixes in the plugin do not reach running loops; v1 ships no update mechanism.

Update (2026-10-22): fixes now reach running loops through `/kucze:perpetua-setup update` ([update.md](../../skills/perpetua-setup/update.md)). The copy and per-loop changes stay; the loop keeps the untouched engine on its `perpetua/engine` branch, and the update merges the new engine against it with git's three-way merge.

# The engine is copied into the loop's repository, not called from the plugin

Setup copies the engine (workflows, scripts, configuration, the policymaker's prompt) into the loop's repository, with an `ENGINE_VERSION` stamp. We rejected thin workflows calling a pinned version of the plugin repo: the runner would need cross-repo access to a private repo, and a setup map's per-loop engine changes would need an override layer. The cost is that fixes in the plugin do not reach running loops; v1 ships no update mechanism.

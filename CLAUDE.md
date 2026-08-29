# CLAUDE.md

Please follow the guidelines and project structure defined in ./AGENTS.md

**This is a fork.** Read [./FORK.md](./FORK.md) before changing backend code,
adding a provider, or touching anything under `ow-patches/`. It covers the patch
system, the rules for when to patch versus edit directly, and the silent failure
modes this fork has actually produced. AGENTS.md is upstream's and does not
mention any of it.

To merge upstream, use the `upstream-reconcile` skill — it encodes the shadow
audit that `ow-patches/check_upstream.py` cannot do on its own.

For Cursor and other agents: Refer to .cursor/rules/ for detailed configuration.

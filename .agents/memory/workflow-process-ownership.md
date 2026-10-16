---
name: Workflow process ownership
description: Port conflicts when a stale Python server survives a workflow restart or reconciliation.
---

When the web workflow reports `Address already in use`, an earlier server process may still own the port even while the workflow itself is failed. Confirm the listener belongs to this project, stop only that stale process, then start the configured workflow once and verify both its state and the port listener.

**Why:** Workflow reconciliation in this Repl attempted to start a duplicate while the earlier application server remained active, so the Preview workflow failed even though the old server still returned pages.

**How to apply:** After a failed restart or merge reconciliation, check the workflow status and process listening on the app port before attempting another restart.

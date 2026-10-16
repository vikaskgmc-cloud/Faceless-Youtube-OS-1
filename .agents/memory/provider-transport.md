---
name: Provider transport constraints
description: Why live provider access uses direct HTTPS rather than connector SDK imports.
---

Keep the pipeline dependency-free; do not silently add SDK requirements to make existing Replit connections work.

**Why:** The user explicitly requested Python standard-library production logic. The suggested Python connector package was unavailable in the configured registry during initial setup. Account-level Firecrawl/ElevenLabs connections did not provide the application with its requested environment secrets.

**How to apply:** Use the existing direct HTTPS adapters and request credentials through the secure secrets flow. If a future supported connector transport is proposed, first reconcile it with the user's standard-library constraint rather than silently changing the runtime.

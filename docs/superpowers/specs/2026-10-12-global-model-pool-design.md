# Global model pool

User request: model settings are a top-level navigation module, alongside experiments. A shared pool supplies execution, experiment and guardian Agents.

The Models page owns model CRUD (display name, model identifier, provider API base, optional Messages API base, token limits and private API key) and scene defaults. RSI retains executor and time-limit controls, with a pool selector instead of embedded model fields. Creating experiments exposes the experiment/guardian pool selections. Offline and mock experiments keep their existing contracts.

Model metadata and credentials live under ignored `.console` state. Keys are never returned, and blank edits preserve the current key. Every key change creates a private immutable credential reference; queued experiments and RSI candidates snapshot model metadata plus that reference. Editing or deleting pool entries cannot silently redirect old work. Bound entries must be reassigned before removal.

Migrate the existing RSI model and its credential once without a model call. Defaults for BKCrab, DSH, experiments and guardians reference the migrated entry. A guardian selection does not enable intervention: observe mode stays observe. Assist mode with a pool model uses a JSONL model driver under the existing supervisor tool and time budgets.

Support official DeepSeek and compatible Chat Completions endpoints; DSH additionally requires a Messages endpoint. Validate URLs and do not accept embedded credentials, query strings or fragments. Never send a model request while merely saving configuration. Existing custom profiles and CLI templates remain supported.

Verification covers migration, CRUD/secrets, immutable snapshots, scene bindings, real adapter configuration, offline preservation, guardian model transport, UI navigation/forms and mobile layout. Deploy only the idle RSI service; preserve cases, experiments and production BKCrab.

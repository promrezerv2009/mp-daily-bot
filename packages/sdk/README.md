# SDK Scaffold

Utility package for generating TypeScript types from the FastAPI OpenAPI schema.

## Setup

```bash
cd packages/sdk
npm install
```

## Generate Types

Run the generator once the API is available locally on `http://localhost:8000`:

```bash
npm run gen
```

Types will be written to `src/types.ts`. Update the `gen` script if your API lives on another host.

## Using the Fetch Helper

```ts
import { apiFetch, setDefaultTenant } from "./src/fetch";

setDefaultTenant("tenant-123");

const summary = await apiFetch("/api/mobile/summary?period=7d");
```

The helper automatically attaches the `X-Tenant-Id` header (configurable via `setDefaultTenant` or `SDK_TENANT_ID` env variable) and ensures JSON requests.

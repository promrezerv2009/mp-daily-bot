export interface ApiClientOptions extends RequestInit {
  baseUrl?: string;
  tenantId?: string;
}

let defaultTenantId: string | undefined;

export function setDefaultTenant(tenantId: string | undefined) {
  defaultTenantId = tenantId;
}

export async function apiFetch<T>(
  path: string,
  options: ApiClientOptions = {},
): Promise<T> {
  const baseUrl = options.baseUrl ?? process.env.SDK_API_BASE_URL ?? "http://localhost:8000";
  const url = `${baseUrl}${path}`;

  const headers = new Headers(options.headers ?? {});
  if (!headers.has("Content-Type")) {
    headers.set("Content-Type", "application/json");
  }

  const tenantId = options.tenantId ?? defaultTenantId ?? process.env.SDK_TENANT_ID;
  if (tenantId && !headers.has("X-Tenant-Id")) {
    headers.set("X-Tenant-Id", tenantId);
  }

  const response = await fetch(url, { ...options, headers });
  if (!response.ok) {
    const body = await response.text();
    throw new Error(`Request failed with ${response.status}: ${body}`);
  }

  if (response.status === 204) {
    return undefined as T;
  }

  return (await response.json()) as T;
}

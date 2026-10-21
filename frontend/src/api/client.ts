import type { components } from "./schema";

export type Schemas = components["schemas"];
export type ApiErrorBody = Schemas["Error"];

export class ApiError extends Error {
  constructor(
    public readonly status: number,
    public readonly code: string,
    message: string,
  ) {
    super(message);
  }
}

const BASE = "/api/v1";

/** Appel JSON avec le cookie de session ; toute réponse en erreur devient une `ApiError`. */
export async function api<T>(path: string, init: RequestInit = {}): Promise<T> {
  const headers = new Headers(init.headers);
  if (init.body !== undefined && !headers.has("Content-Type")) {
    headers.set("Content-Type", "application/json");
  }
  const response = await fetch(`${BASE}${path}`, { ...init, headers, credentials: "include" });
  if (!response.ok) {
    let body: Partial<ApiErrorBody> = {};
    try {
      body = (await response.json()) as Partial<ApiErrorBody>;
    } catch {
      // corps vide ou non JSON : on garde le statut
    }
    throw new ApiError(
      response.status,
      body.code ?? "error",
      body.message ?? `Erreur ${response.status}`,
    );
  }
  if (response.status === 204) return undefined as T;
  return (await response.json()) as T;
}

export const post = <T>(path: string, body?: unknown): Promise<T> =>
  api<T>(path, { method: "POST", body: body === undefined ? undefined : JSON.stringify(body) });

export const put = <T>(path: string, body: unknown): Promise<T> =>
  api<T>(path, { method: "PUT", body: JSON.stringify(body) });

export const patch = <T>(path: string, body: unknown): Promise<T> =>
  api<T>(path, { method: "PATCH", body: JSON.stringify(body) });

export const del = (path: string): Promise<void> => api<void>(path, { method: "DELETE" });

export const LOGIN_URL = `${BASE}/auth/login`;

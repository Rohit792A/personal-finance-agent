/**
 * Error thrown by every API call. `message` is user-facing (the backend's `detail` when it sends one),
 * because the legacy screens show `error.message` in toasts.
 *
 * status: HTTP status, 0 = network failure (offline, DNS, CORS), 408 = client-side timeout.
 */
export class ApiError extends Error {
  readonly status: number;
  readonly data: unknown;

  constructor(message: string, status: number, data: unknown = null) {
    super(message);
    this.name = 'ApiError';
    this.status = status;
    this.data = data;
  }
}

export const isApiError = (error: unknown): error is ApiError => error instanceof ApiError;

/**
 * The single HTTP client for every backend call (React shell and legacy screens).
 *
 * - Adds `Authorization: Bearer <Firebase ID token>` to every request.
 * - Sends requests to the backend of the signed-in account (set with setApiSession).
 * - 401: retries once with a force-refreshed token, then signs the user out.
 * - 403: the error carries the backend detail; outside /me and the Gmail feature routes the shell is
 *   asked to re-check access (the account may have been removed from the allowlist).
 * - 429 and every other error: ApiError whose message is the backend's `detail`, so the legacy
 *   screens' toasts show it.
 * - Recorded demo builds (src/recorded/mode.ts) never reach the network: every request is answered
 *   from the recorded fixtures (src/recorded/transport.ts), with the same error handling.
 */
import { RECORDED_DEMO } from '../recorded/mode';
import { answerFromRecording } from '../recorded/transport';
import { DEFAULT_TIMEOUT_MS } from './config';
import { ApiError } from './errors';

export interface ApiSession {
  /** Backend origin without a trailing slash. '' = same origin (Vite proxy in development). */
  readonly baseUrl: string;
  /** Firebase ID token; forceRefresh=true always fetches a new one. */
  getIdToken(forceRefresh: boolean): Promise<string>;
  /** The token was rejected even after a forced refresh: sign out and show this message. */
  onSessionExpired(message: string): void;
  /** A 403 on a normal route: re-check whether this account still has access. */
  onForbidden(): void;
}

export interface ApiRequestOptions {
  method?: string;
  /** Plain objects and arrays are sent as JSON; FormData, Blob, URLSearchParams and strings as-is. */
  body?: unknown;
  headers?: HeadersInit;
  /** Milliseconds before the request is aborted with an ApiError(408). */
  timeout?: number;
  signal?: AbortSignal;
}

export interface BlobResult {
  blob: Blob;
  contentType: string;
  /** From Content-Disposition, when the backend exposes it to the browser (CORS). */
  fileName: string | null;
}

export const SESSION_EXPIRED_MESSAGE = 'Your session has expired. Please sign in again.';
const SIGNED_OUT_MESSAGE = 'You are signed out. Please sign in again.';
const NETWORK_MESSAGE = "Can't reach the server. Check your connection and try again.";

let activeSession: ApiSession | null = null;
const tokenRefreshes = new WeakMap<ApiSession, Promise<string>>();
const expiredSessions = new WeakSet<ApiSession>();

/** Binds the API client to the signed-in account (null on sign-out). */
export function setApiSession(session: ApiSession | null): void {
  activeSession = session;
}

const isRecord = (value: unknown): value is Record<string, unknown> =>
  typeof value === 'object' && value !== null && !Array.isArray(value);

function defaultMessage(status: number): string {
  if (status === 401) return SESSION_EXPIRED_MESSAGE;
  if (status === 403) return "This account doesn't have access.";
  if (status === 404) return 'Not found.';
  if (status === 429) return "You've reached today's limit. Try again tomorrow.";
  if (status >= 500) return 'The server had a problem. Try again in a moment.';
  return `Request failed (HTTP ${status}).`;
}

/** Turns FastAPI / generic error bodies into one readable message. */
export function errorMessageFrom(status: number, data: unknown): string {
  if (isRecord(data)) {
    const { detail, message, error } = data;
    if (typeof detail === 'string' && detail.trim()) return detail.trim();
    if (Array.isArray(detail)) {
      // FastAPI validation errors: [{ loc: ['body', 'amount'], msg: '...' }]
      const parts = detail.filter(isRecord).map((item) => {
        const loc = Array.isArray(item.loc) ? item.loc.slice(1).map(String).join('.') : '';
        const msg = typeof item.msg === 'string' ? item.msg : 'invalid value';
        return loc ? `${loc}: ${msg}` : msg;
      });
      if (parts.length > 0) return parts.join(', ');
    }
    if (isRecord(detail) && typeof detail.message === 'string' && detail.message.trim()) return detail.message.trim();
    if (typeof message === 'string' && message.trim()) return message.trim();
    if (typeof error === 'string' && error.trim()) return error.trim();
  } else if (typeof data === 'string') {
    const text = data.trim();
    // Never surface HTML error pages (proxies, load balancers) in the UI.
    if (text && text.length <= 300 && !text.startsWith('<')) return text;
  }
  return defaultMessage(status);
}

function encodeBody(body: unknown, headers: Headers): BodyInit | undefined {
  if (body === undefined || body === null) return undefined;
  if (body instanceof FormData || body instanceof Blob || body instanceof URLSearchParams || typeof body === 'string') {
    return body;
  }
  headers.set('Content-Type', 'application/json');
  return JSON.stringify(body);
}

async function readBody(response: Response): Promise<unknown> {
  if (response.status === 204) return null;
  const contentType = response.headers.get('content-type') ?? '';
  if (contentType.includes('application/json')) {
    try {
      return (await response.json()) as unknown;
    } catch {
      return null;
    }
  }
  return response.text();
}

function expireSession(session: ApiSession): void {
  if (expiredSessions.has(session)) return;
  expiredSessions.add(session);
  if (activeSession === session) session.onSessionExpired(SESSION_EXPIRED_MESSAGE);
}

/** One forced refresh per session at a time, shared by every request that got a 401. */
function refreshToken(session: ApiSession): Promise<string> {
  let pending = tokenRefreshes.get(session);
  if (!pending) {
    pending = session.getIdToken(true).finally(() => tokenRefreshes.delete(session));
    tokenRefreshes.set(session, pending);
  }
  return pending;
}

/** 403s that are expected feature switches, not a lost account. */
const isFeatureGatedPath = (path: string): boolean => path === '/me' || path.startsWith('/api/gmail/');

interface RawResult {
  response: Response;
  data: unknown;
}

async function sendOnce(
  url: string,
  token: string,
  options: ApiRequestOptions,
  expectBlob: boolean,
): Promise<RawResult> {
  const headers = new Headers(options.headers);
  if (!headers.has('Accept')) headers.set('Accept', expectBlob ? '*/*' : 'application/json');
  headers.set('Authorization', `Bearer ${token}`);
  const body = encodeBody(options.body, headers);

  const timeout = options.timeout ?? DEFAULT_TIMEOUT_MS;
  const controller = new AbortController();
  const timeoutReason = new DOMException('Request timed out', 'TimeoutError');
  const timer = setTimeout(() => {
    controller.abort(timeoutReason);
  }, timeout);
  const onCallerAbort = () => {
    controller.abort();
  };
  options.signal?.addEventListener('abort', onCallerAbort, { once: true });

  try {
    const response = await fetch(url, {
      method: options.method ?? 'GET',
      headers,
      body: body ?? null,
      cache: 'no-store',
      // 'same-origin', not 'omit': Cloud Shell web preview's proxy needs its own cookie on every
      // same-origin request (Vite proxy mode) or it redirects the call, which fetch reports as a
      // network error. Calls to the Cloud Run backends are cross-origin, so they still send no
      // cookies; the backend only trusts the Bearer token.
      credentials: 'same-origin',
      signal: controller.signal,
    });
    const data = response.ok && expectBlob ? await response.blob() : await readBody(response);
    return { response, data };
  } catch (error) {
    if (controller.signal.reason === timeoutReason) throw new ApiError(`The request timed out after ${Math.round(timeout / 1000)}s.`, 408);
    if (options.signal?.aborted) throw error; // cancelled by the caller
    throw new ApiError(NETWORK_MESSAGE, 0);
  } finally {
    clearTimeout(timer);
    options.signal?.removeEventListener('abort', onCallerAbort);
  }
}

/**
 * Recorded demo builds: the answer from the recorded fixtures, with the same error handling. The
 * network code below isn't part of those builds at all.
 */
async function requestFromRecording(endpoint: string, options: ApiRequestOptions): Promise<RawResult> {
  const recorded = await answerFromRecording(endpoint, options);
  if (!recorded.response.ok) {
    throw new ApiError(errorMessageFrom(recorded.response.status, recorded.data), recorded.response.status, recorded.data);
  }
  return recorded;
}

function request(endpoint: string, options: ApiRequestOptions, expectBlob: boolean): Promise<RawResult> {
  return RECORDED_DEMO ? requestFromRecording(endpoint, options) : requestFromBackend(endpoint, options, expectBlob);
}

async function requestFromBackend(endpoint: string, options: ApiRequestOptions, expectBlob: boolean): Promise<RawResult> {
  const session = activeSession;
  if (!session) throw new ApiError(SIGNED_OUT_MESSAGE, 401);

  const path = endpoint.startsWith('/') ? endpoint : `/${endpoint}`;
  const url = `${session.baseUrl}${path}`;

  let token: string;
  try {
    token = await session.getIdToken(false);
  } catch {
    expireSession(session);
    throw new ApiError(SESSION_EXPIRED_MESSAGE, 401);
  }

  let result = await sendOnce(url, token, options, expectBlob);

  if (result.response.status === 401) {
    let freshToken: string | null = null;
    try {
      freshToken = await refreshToken(session);
    } catch {
      freshToken = null;
    }
    if (freshToken) result = await sendOnce(url, freshToken, options, expectBlob);
    if (!freshToken || result.response.status === 401) {
      expireSession(session);
      throw new ApiError(SESSION_EXPIRED_MESSAGE, 401, result.data);
    }
  }

  const { response, data } = result;
  if (!response.ok) {
    if (response.status === 403 && !isFeatureGatedPath(path) && activeSession === session) {
      session.onForbidden();
    }
    throw new ApiError(errorMessageFrom(response.status, data), response.status, data);
  }
  return result;
}

/** JSON (or text) request. Resolves with the parsed body; rejects with ApiError. */
export async function apiClient(endpoint: string, options: ApiRequestOptions = {}): Promise<unknown> {
  const { data } = await request(endpoint, options, false);
  return data;
}

/** Binary request (PDF downloads, audio) with the same authentication and error handling. */
export async function apiFetchBlob(endpoint: string, options: ApiRequestOptions = {}): Promise<BlobResult> {
  const { response, data } = await request(endpoint, options, true);
  return {
    blob: data instanceof Blob ? data : new Blob([]),
    contentType: response.headers.get('content-type') ?? '',
    fileName: fileNameFromDisposition(response.headers.get('content-disposition')),
  };
}

/** Parses `attachment; filename*=UTF-8''name.pdf` or `filename="name.pdf"`. */
export function fileNameFromDisposition(header: string | null): string | null {
  if (!header) return null;
  const encoded = /filename\*\s*=\s*(?:UTF-8|utf-8)''([^;]+)/.exec(header)?.[1];
  if (encoded) {
    try {
      return decodeURIComponent(encoded.trim().replace(/^"|"$/g, ''));
    } catch {
      // fall through to the plain filename
    }
  }
  const plain = /filename\s*=\s*"([^"]*)"|filename\s*=\s*([^;]+)/.exec(header);
  const name = plain?.[1] ?? plain?.[2];
  return name ? name.trim() : null;
}

/** Saves a Blob as a file download with the given name. */
export function saveBlob(blob: Blob, fileName: string): void {
  // eslint-disable-next-line no-control-regex -- strip control characters from file names
  const safeName = fileName.replace(/[\\/:*?"<>|\u0000-\u001f]/g, '_').slice(0, 200) || 'download';
  const url = URL.createObjectURL(blob);
  const link = document.createElement('a');
  link.href = url;
  link.download = safeName;
  link.rel = 'noopener';
  link.style.display = 'none';
  document.body.append(link);
  link.click();
  link.remove();
  // Revoke later so the browser has started the download first.
  window.setTimeout(() => {
    URL.revokeObjectURL(url);
  }, 1000);
}

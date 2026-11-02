/** GET /me: who is signed in, which environment this backend is, and what it allows. */
import { apiClient } from './client';
import { API_ENDPOINTS } from './config';
import { ApiError } from './errors';

export type Environment = 'personal' | 'demo';

export interface AiLimit {
  limit: number;
  used: number;
  remaining: number;
}

export interface Features {
  gmail_sync: boolean;
  uploads: boolean;
  ai: boolean;
}

export interface Me {
  email: string;
  uid: string;
  provider: string;
  environment: Environment;
  features: Features;
  /** Daily AI quotas per feature (demo), or null when unlimited. */
  ai_limits: Record<string, AiLimit> | null;
  data_resets_daily: boolean;
}

const isRecord = (value: unknown): value is Record<string, unknown> =>
  typeof value === 'object' && value !== null && !Array.isArray(value);

const asNumber = (value: unknown): number | null => (typeof value === 'number' && Number.isFinite(value) ? value : null);

function parseAiLimits(value: unknown): Record<string, AiLimit> | null {
  if (!isRecord(value)) return null;
  const limits: Record<string, AiLimit> = {};
  for (const [feature, raw] of Object.entries(value)) {
    if (!isRecord(raw)) continue;
    const limit = asNumber(raw.limit);
    const used = asNumber(raw.used);
    const remaining = asNumber(raw.remaining);
    if (limit === null || used === null || remaining === null) continue;
    limits[feature] = { limit, used, remaining };
  }
  return limits;
}

/** Validates the /me payload so the shell never renders from a malformed response. */
export function parseMe(data: unknown): Me {
  if (!isRecord(data) || (data.environment !== 'personal' && data.environment !== 'demo')) {
    throw new ApiError('The server sent an unexpected response.', 502, data);
  }
  const features = isRecord(data.features) ? data.features : {};
  return {
    email: typeof data.email === 'string' ? data.email : '',
    uid: typeof data.uid === 'string' ? data.uid : '',
    provider: typeof data.provider === 'string' ? data.provider : '',
    environment: data.environment,
    features: {
      gmail_sync: features.gmail_sync === true,
      uploads: features.uploads === true,
      ai: features.ai === true,
    },
    ai_limits: parseAiLimits(data.ai_limits),
    data_resets_daily: data.data_resets_daily === true,
  };
}

export async function getMe(): Promise<Me> {
  return parseMe(await apiClient(API_ENDPOINTS.ME, { timeout: 30_000 }));
}

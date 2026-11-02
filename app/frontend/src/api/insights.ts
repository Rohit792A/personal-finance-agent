/** AI CFO advisor: one-shot insights and multi-turn chat (daily-limited in the demo: HTTP 429). */
import { apiClient } from './client';
import { AI_TIMEOUT_MS, API_ENDPOINTS } from './config';

const isRecord = (value: unknown): value is Record<string, unknown> =>
  typeof value === 'object' && value !== null && !Array.isArray(value);

export async function getAiInsights(): Promise<{ insights: string }> {
  const data = await apiClient(API_ENDPOINTS.INSIGHTS, { timeout: AI_TIMEOUT_MS });
  const insights = isRecord(data) && typeof data.insights === 'string' ? data.insights : '';
  return { insights: insights || 'No insights returned from AI Advisor.' };
}

export async function sendAdvisorChatMessage(
  message: string,
  history: { role: string; content: string }[] = [],
): Promise<{ reply: string }> {
  const data = await apiClient(API_ENDPOINTS.INSIGHTS_CHAT, {
    method: 'POST',
    body: { message, history },
    timeout: AI_TIMEOUT_MS,
  });
  const reply = isRecord(data) && typeof data.reply === 'string' ? data.reply : '';
  return { reply: reply || 'No response returned from AI Advisor.' };
}

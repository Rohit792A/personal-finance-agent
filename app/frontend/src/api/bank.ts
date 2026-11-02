/**
 * Gmail bank sync and exported statement PDFs (personal environment only; the demo backend
 * answers these routes with 403 "Gmail sync is turned off in the demo.").
 */
import { apiClient, apiFetchBlob, saveBlob } from './client';
import { API_ENDPOINTS, IMPORT_TIMEOUT_MS, LONG_TIMEOUT_MS } from './config';

type Id = number | string;

export interface GmailSettingsInput {
  email_address: string;
  app_password?: string;
  pdf_password?: string;
  allowed_senders?: string;
  auto_sync_enabled?: boolean;
  sync_interval_hours?: number;
}

export async function fetchGmailSyncSettings(): Promise<unknown> {
  return apiClient(API_ENDPOINTS.GMAIL_SETTINGS);
}

export async function saveGmailSyncSettings({
  email_address,
  app_password,
  pdf_password = '',
  allowed_senders,
  auto_sync_enabled = true,
  sync_interval_hours = 24,
}: GmailSettingsInput): Promise<unknown> {
  return apiClient(API_ENDPOINTS.GMAIL_SETTINGS, {
    method: 'POST',
    body: { email_address, app_password, pdf_password, allowed_senders, auto_sync_enabled, sync_interval_hours },
  });
}

export async function triggerGmailSync({ days_back = 30, include_statements = true } = {}): Promise<unknown> {
  return apiClient(API_ENDPOINTS.GMAIL_SYNC, {
    method: 'POST',
    body: { days_back, include_statements },
    timeout: IMPORT_TIMEOUT_MS,
  });
}

export async function fetchExportedStatementPdfs(): Promise<unknown> {
  return apiClient(API_ENDPOINTS.GMAIL_STATEMENTS);
}

/**
 * Unlocks and imports an exported statement. An empty password retries a statement that is
 * already unlocked but wasn't imported (sync_status IMPORT_FAILED / EXPORTED_ONLY).
 */
export async function unlockExportedStatementPdf(stmtId: Id, password = ''): Promise<unknown> {
  return apiClient(API_ENDPOINTS.GMAIL_STATEMENT_UNLOCK(stmtId), {
    method: 'POST',
    body: { password },
    timeout: IMPORT_TIMEOUT_MS,
  });
}

/**
 * Downloads a statement PDF with the sign-in token (a plain link could not send it) and saves it
 * under the statement's file name.
 */
export async function downloadStatementPdf(stmtId: Id, fileName?: string): Promise<void> {
  const result = await apiFetchBlob(API_ENDPOINTS.GMAIL_STATEMENT_DOWNLOAD(stmtId), {
    timeout: LONG_TIMEOUT_MS,
    headers: { Accept: 'application/pdf, */*' },
  });
  saveBlob(result.blob, fileName || result.fileName || `statement-${String(stmtId)}.pdf`);
}

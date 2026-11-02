/** Document upload (PDF, Excel, receipt images) for AI extraction. */
import { apiClient } from './client';
import { API_ENDPOINTS, IMPORT_TIMEOUT_MS } from './config';

const isRecord = (value: unknown): value is Record<string, unknown> =>
  typeof value === 'object' && value !== null && !Array.isArray(value);

export interface UploadResult {
  status: string;
  message: string;
  notifications: string[];
}

/**
 * Uploads a document for AI extraction. `password` opens a password-protected PDF: the backend
 * uses it in memory only and never stores it (DEC-060).
 */
export async function uploadDocument(file: File | null | undefined, password = ''): Promise<UploadResult> {
  if (!file) throw new Error('Please select a file to upload.');

  const formData = new FormData();
  formData.append('file', file, file.name);
  if (password) formData.append('password', password);

  // Multi-page PDFs can take minutes of AI processing (read a few pages per call, DEC-060).
  const data = await apiClient(API_ENDPOINTS.UPLOAD, { method: 'POST', body: formData, timeout: IMPORT_TIMEOUT_MS });
  const body = isRecord(data) ? data : {};
  const message = typeof body.message === 'string' && body.message ? body.message : `Processed ${file.name}`;
  const notifications = Array.isArray(body.notifications)
    ? body.notifications.map((item) => String(item))
    : [typeof body.message === 'string' && body.message ? body.message : `Successfully processed ${file.name}`];

  return {
    status: typeof body.status === 'string' && body.status ? body.status : 'success',
    message,
    notifications,
  };
}

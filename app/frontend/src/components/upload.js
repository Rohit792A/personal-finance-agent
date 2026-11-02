/**
 * Upload Component
 * File input, drag-and-drop zone, POST /upload via FormData, loading states, and toast notifications.
 */

import { uploadDocument, getTransactions } from '../api/index.ts';
import { toast } from './toast.js';

/**
 * Format bytes to readable size string.
 * @param {number} bytes
 * @returns {string}
 */
function formatFileSize(bytes) {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  return `${(bytes / (1024 * 1024)).toFixed(2)} MB`;
}

/**
 * Mount the Upload Component into target container.
 * 
 * @param {HTMLElement} container
 * @param {Object} options
 * @param {() => void} [options.onUploadSuccess] - Callback after successful upload and ledger sync
 */
export function initUploadComponent(container, { onUploadSuccess } = {}) {
  container.innerHTML = `
    <div class="bg-slate-900 border border-slate-800 rounded-2xl p-6 shadow-xl transition-all">
      <div class="flex items-center gap-3 mb-4">
        <div class="p-2.5 rounded-xl bg-indigo-500/10 text-indigo-400 border border-indigo-500/20">
          <svg class="w-5 h-5" fill="none" viewBox="0 0 24 24" stroke="currentColor">
            <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M7 16a4 4 0 01-.88-7.903A5 5 0 1115.9 6L16 6a5 5 0 011 9.9M15 13l-3-3m0 0l-3 3m3-3v12" />
          </svg>
        </div>
        <div>
          <h2 class="text-base font-semibold text-white">Upload Financial Documents</h2>
          <p class="text-xs text-slate-400">Upload bank statements, receipts, PDF, or Excel sheets</p>
        </div>
      </div>

      <div
        id="drop-zone"
        class="border-2 border-dashed border-slate-700 hover:border-indigo-500/60 rounded-xl p-6 text-center cursor-pointer transition-colors bg-slate-950/40 hover:bg-slate-950/70 group"
      >
        <input
          type="file"
          id="file-input"
          class="hidden"
          accept=".pdf,.xlsx,.xls,.png,.jpg,.jpeg"
        />

        <div id="drop-zone-prompt" class="flex flex-col items-center justify-center space-y-2">
          <div class="p-3 rounded-full bg-slate-800/80 group-hover:bg-indigo-500/20 text-slate-400 group-hover:text-indigo-300 transition-colors">
            <svg class="w-6 h-6" fill="none" viewBox="0 0 24 24" stroke="currentColor">
              <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M4 16v1a3 3 0 003 3h10a3 3 0 003-3v-1m-4-8l-4-4m0 0L8 8m4-4v12" />
            </svg>
          </div>
          <p class="text-sm font-medium text-slate-300">
            <span class="text-indigo-400 group-hover:underline">Click to browse</span> or drag and drop
          </p>
          <p class="text-xs text-slate-500">Supported: PDF, Excel (.xlsx, .xls), Receipts</p>
        </div>

        <!-- Selected File Preview -->
        <div id="file-preview" class="hidden flex items-center justify-between p-3 bg-slate-800/60 rounded-xl border border-slate-700/60">
          <div class="flex items-center gap-3 overflow-hidden">
            <div class="p-2 rounded-lg bg-indigo-500/20 text-indigo-300 flex-shrink-0">
              <svg class="w-5 h-5" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M9 12h6m-6 4h6m2 5H7a2 2 0 01-2-2V5a2 2 0 012-2h5.586a1 1 0 01.707.293l5.414 5.414a1 1 0 01.293.707V19a2 2 0 01-2 2z" />
              </svg>
            </div>
            <div class="text-left truncate">
              <p id="file-name" class="text-sm font-medium text-white truncate"></p>
              <p id="file-size" class="text-xs text-slate-400"></p>
            </div>
          </div>
          <button
            type="button"
            id="clear-file-btn"
            class="text-slate-400 hover:text-rose-400 p-1 rounded-lg hover:bg-slate-700/60 transition-colors"
            title="Remove file"
          >
            <svg class="w-5 h-5" fill="none" viewBox="0 0 24 24" stroke="currentColor">
              <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M6 18L18 6M6 6l12 12" />
            </svg>
          </button>
        </div>
      </div>

      <!-- PDF password: shown for PDFs; bank statements are often locked (DEC-060) -->
      <div id="pdf-password-row" class="hidden mt-3">
        <label for="pdf-password-input" class="block text-xs text-slate-400 mb-1">PDF password (only if the file asks for one when you open it)</label>
        <input
          type="password"
          id="pdf-password-input"
          autocomplete="off"
          placeholder="Leave empty if the PDF isn't password-protected"
          class="w-full bg-slate-900 border border-slate-700 rounded-lg px-3 py-2 text-sm text-slate-200 font-mono"
        />
      </div>

      <!-- AI Agent Progress Bar State -->
      <div id="upload-progress-box" class="hidden mt-3 p-3.5 rounded-xl bg-indigo-950/30 border border-indigo-500/30 space-y-2">
        <div class="flex items-center justify-between text-xs">
          <span class="font-semibold text-indigo-300 flex items-center gap-2">
            <span class="w-2 h-2 rounded-full bg-indigo-400 animate-ping"></span>
            <span id="upload-progress-stage">AI Agents processing document...</span>
          </span>
          <span class="text-[11px] font-mono tabular-nums text-indigo-400">Gemini 2.5 Flash</span>
        </div>
        <div class="w-full h-1.5 bg-slate-800 rounded-full overflow-hidden">
          <div class="h-full bg-gradient-to-r from-indigo-500 via-violet-500 to-emerald-400 rounded-full animate-pulse w-4/5"></div>
        </div>
      </div>

      <!-- Action Bar -->
      <div class="mt-4 flex items-center justify-between">
        <span class="text-xs text-slate-500" id="upload-status-hint">No file selected</span>
        <button
          type="button"
          id="upload-btn"
          disabled
          class="inline-flex items-center justify-center gap-2 px-5 py-2.5 rounded-xl font-medium text-sm text-white bg-indigo-600 hover:bg-indigo-500 active:bg-indigo-700 transition-all shadow-lg shadow-indigo-600/20 disabled:opacity-40 disabled:cursor-not-allowed cursor-pointer"
        >
          <svg id="upload-btn-icon" class="w-4 h-4" fill="none" viewBox="0 0 24 24" stroke="currentColor">
            <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M4 16v1a3 3 0 003 3h10a3 3 0 003-3v-1m-4-8l-4-4m0 0L8 8m4-4v12" />
          </svg>
          <svg id="upload-btn-spinner" class="hidden animate-spin w-4 h-4 text-white" fill="none" viewBox="0 0 24 24">
            <circle class="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" stroke-width="4"></circle>
            <path class="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4zm2 5.291A7.962 7.962 0 014 12H0c0 3.042 1.135 5.824 3 7.938l3-2.647z"></path>
          </svg>
          <span id="upload-btn-text">Upload Document</span>
        </button>
      </div>
    </div>
  `;

  const dropZone = container.querySelector('#drop-zone');
  const fileInput = container.querySelector('#file-input');
  const dropZonePrompt = container.querySelector('#drop-zone-prompt');
  const filePreview = container.querySelector('#file-preview');
  const fileNameEl = container.querySelector('#file-name');
  const fileSizeEl = container.querySelector('#file-size');
  const clearFileBtn = container.querySelector('#clear-file-btn');
  const uploadBtn = container.querySelector('#upload-btn');
  const uploadBtnIcon = container.querySelector('#upload-btn-icon');
  const uploadBtnSpinner = container.querySelector('#upload-btn-spinner');
  const uploadBtnText = container.querySelector('#upload-btn-text');
  const statusHint = container.querySelector('#upload-status-hint');
  const progressBox = container.querySelector('#upload-progress-box');
  const passwordRow = container.querySelector('#pdf-password-row');
  const passwordInput = container.querySelector('#pdf-password-input');

  let selectedFile = null;
  let isUploading = false;

  const isPdf = (file) => Boolean(file && /\.pdf$/i.test(file.name || ''));

  function setSelectedFile(file) {
    selectedFile = file;
    passwordInput.value = '';
    passwordRow.classList.toggle('hidden', !isPdf(file));
    if (file) {
      fileNameEl.textContent = file.name;
      fileSizeEl.textContent = formatFileSize(file.size);
      dropZonePrompt.classList.add('hidden');
      filePreview.classList.remove('hidden');
      uploadBtn.disabled = false;
      statusHint.textContent = 'Ready to process with AI agent';
      statusHint.className = 'text-xs text-indigo-400 font-medium';
    } else {
      fileInput.value = '';
      dropZonePrompt.classList.remove('hidden');
      filePreview.classList.add('hidden');
      uploadBtn.disabled = true;
      statusHint.textContent = 'No file selected';
      statusHint.className = 'text-xs text-slate-500';
    }
  }

  function setUploading(loading) {
    isUploading = loading;
    uploadBtn.disabled = loading;
    fileInput.disabled = loading;
    clearFileBtn.disabled = loading;
    passwordInput.disabled = loading;

    if (loading) {
      uploadBtnIcon.classList.add('hidden');
      uploadBtnSpinner.classList.remove('hidden');
      uploadBtnText.textContent = 'AI Processing...';
      if (progressBox) progressBox.classList.remove('hidden');
      statusHint.textContent = 'AI Agents processing document...';
      statusHint.className = 'text-xs text-amber-400 animate-pulse font-medium';
    } else {
      uploadBtnIcon.classList.remove('hidden');
      uploadBtnSpinner.classList.add('hidden');
      uploadBtnText.textContent = 'Upload Document';
      if (progressBox) progressBox.classList.add('hidden');
    }
  }

  // Click on dropzone opens native file picker
  dropZone.addEventListener('click', (e) => {
    if (isUploading) return;
    if (e.target.closest('#clear-file-btn')) return;
    fileInput.click();
  });

  fileInput.addEventListener('change', () => {
    if (fileInput.files && fileInput.files[0]) {
      setSelectedFile(fileInput.files[0]);
    }
  });

  clearFileBtn.addEventListener('click', (e) => {
    e.stopPropagation();
    if (isUploading) return;
    setSelectedFile(null);
  });

  // Drag and drop handlers
  ['dragenter', 'dragover'].forEach((eventName) => {
    dropZone.addEventListener(eventName, (e) => {
      e.preventDefault();
      e.stopPropagation();
      if (!isUploading) {
        dropZone.classList.add('border-indigo-500', 'bg-indigo-950/20');
      }
    });
  });

  ['dragleave', 'drop'].forEach((eventName) => {
    dropZone.addEventListener(eventName, (e) => {
      e.preventDefault();
      e.stopPropagation();
      dropZone.classList.remove('border-indigo-500', 'bg-indigo-950/20');
    });
  });

  dropZone.addEventListener('drop', (e) => {
    if (isUploading) return;
    const dt = e.dataTransfer;
    if (dt && dt.files && dt.files.length > 0) {
      setSelectedFile(dt.files[0]);
    }
  });

  // Upload handler
  uploadBtn.addEventListener('click', async () => {
    if (!selectedFile || isUploading) return;

    // Ledger size before this upload, so the fallback below can tell whether rows were really added.
    let baselineCount = null;

    try {
      setUploading(true);

      baselineCount = await getTransactions()
        .then((items) => items.length)
        .catch(() => null);

      const response = await uploadDocument(selectedFile, isPdf(selectedFile) ? passwordInput.value : '');

      // Render notifications array from the JSON response as user-friendly toast alerts
      if (Array.isArray(response.notifications) && response.notifications.length > 0) {
        toast.showBatch(response.notifications, 'success', 350);
      } else {
        toast.show(response.message || 'File uploaded successfully!', 'success');
      }

      // Reset selection
      setSelectedFile(null);

      // Callback to refresh ledger data dynamically
      if (typeof onUploadSuccess === 'function') {
        onUploadSuccess();
      }
    } catch (error) {
      console.warn('[Upload] Upload response error or timeout:', error);
      // A 4xx answer (e.g. a password-protected PDF) is final: say why right away instead of
      // polling the ledger. 408 is the client timeout, handled below.
      if (error.status >= 400 && error.status < 500 && error.status !== 408) {
        toast.show(error.message || 'The file could not be imported.', 'error', 10000);
        statusHint.textContent = error.message ? `Error: ${error.message}` : 'Upload failed. Please retry.';
        statusHint.className = 'text-xs text-rose-400 font-medium';
        if (/password/i.test(error.message || '') && isPdf(selectedFile)) {
          passwordRow.classList.remove('hidden');
          passwordInput.focus();
        }
        return;
      }
      statusHint.textContent = 'Verifying AI extraction with ledger...';
      statusHint.className = 'text-xs text-amber-400 font-medium animate-pulse';

      // Verify if transactions were extracted in the background
      let verified = false;
      for (let attempt = 0; attempt < 3; attempt++) {
        await new Promise((r) => setTimeout(r, 2000));
        try {
          const fresh = await getTransactions();
          // A non-empty ledger proves nothing; only treat it as verified if this upload added rows.
          if (baselineCount !== null && fresh.length > baselineCount) {
            verified = true;
            break;
          }
        } catch {
          // continue polling
        }
      }

      if (verified) {
        toast.show('AI document processing completed in background! Financial ledger updated.', 'success', 7000);
        statusHint.textContent = 'AI extraction completed and verified in ledger.';
        statusHint.className = 'text-xs text-emerald-400 font-medium';
        setSelectedFile(null);
        if (typeof onUploadSuccess === 'function') {
          onUploadSuccess();
        }
      } else {
        const isTimeout = error.status === 408 || error.status === 504 || error.message?.includes('timed out');
        if (isTimeout) {
          toast.show('AI processing is taking longer than usual. Check the Transactions page shortly as extraction runs in background.', 'warning', 8000);
          statusHint.textContent = 'Processing continues in background. Please refresh shortly.';
          statusHint.className = 'text-xs text-amber-400 font-medium';
        } else {
          toast.show(error.message || 'Failed to upload document. Please check backend connection.', 'error', 7000);
          statusHint.textContent = error.message ? `Error: ${error.message}` : 'Upload failed. Please retry.';
          statusHint.className = 'text-xs text-rose-400 font-medium';
        }
      }
    } finally {
      setUploading(false);
    }
  });
}

/**
 * AI CFO Insights Component
 * Fetches GET /insights, renders loading spinner, parses markdown via `marked` (sanitized with DOMPurify),
 * and provides a stateless Multi-Turn chat interface backed by Gemini on Vertex AI (Flash-Lite / Flash tiers,
 * chosen by the backend's advisor; Gemini 3.5 by default).
 */

import { getAiInsights, sendAdvisorChatMessage } from '../api/index.ts';
import { recordedChatQuestions } from '../recorded/manifest.ts';
import { RECORDED_DEMO } from '../recorded/mode.ts';
import { escapeHtml } from '../utils/dom.js';
import { renderMarkdown } from '../utils/markdown.js';
import { toast } from './toast.js';

const QUICK_PROMPT_CLASS =
  'advisor-quick-prompt px-2.5 py-1 rounded-lg bg-slate-800 hover:bg-slate-700 text-slate-300 text-[11px] border border-slate-700 transition-colors cursor-pointer';

/**
 * Recorded demo (src/recorded/): the chat's suggestion chips become the questions whose answers were
 * recorded; other questions get a note listing them.
 */
function showRecordedQuestions(container) {
  const intro = container.querySelector('#advisor-chat-starter p');
  const chips = container.querySelector('#advisor-chat-starter .flex');
  const input = container.querySelector('#advisor-chat-input');
  if (intro) {
    intro.innerHTML =
      "💡 <strong>Recorded demo:</strong> pick a question to replay the AI CFO's answer, recorded from Gemini on the demo data.";
  }
  if (chips) {
    chips.innerHTML = recordedChatQuestions()
      .map((question) => `<button type="button" class="${QUICK_PROMPT_CLASS}">${escapeHtml(question)}</button>`)
      .join('');
  }
  if (input) input.placeholder = 'This recorded demo replays the questions above';
}

/**
 * Mount the AI CFO Insights & Multi-Turn Conversational Component.
 * 
 * @param {HTMLElement} container
 */
export function initInsightsComponent(container) {
  container.innerHTML = `
    <div class="bg-slate-900 border border-slate-800 border-t-4 border-t-violet-500 rounded-2xl p-6 shadow-xl flex flex-col h-full">
      <!-- Card Header -->
      <div class="flex items-start justify-between pb-4 border-b border-slate-800 mb-5 gap-4">
        <div class="flex items-center gap-3">
          <div class="p-2.5 rounded-xl bg-violet-500/10 text-violet-400 border border-violet-500/20">
            <svg class="w-5 h-5" fill="none" viewBox="0 0 24 24" stroke="currentColor">
              <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M13 10V3L4 14h7v7l9-11h-7z" />
            </svg>
          </div>
          <div>
            <div class="flex items-center gap-2">
              <h2 class="text-base font-semibold text-white">AI CFO Advisor Insights</h2>
              <span class="px-2 py-0.5 text-[10px] uppercase font-bold tracking-wider rounded-full bg-violet-500/15 text-violet-300 border border-violet-500/30">
                Agentic AI
              </span>
            </div>
            <p id="insights-timestamp" class="text-xs text-slate-400">Autonomous financial intelligence & multi-turn advisory</p>
          </div>
        </div>

        <!-- Trigger Button -->
        <button
          type="button"
          id="fetch-insights-btn"
          class="inline-flex items-center justify-center gap-2 px-4 py-2 text-xs font-semibold rounded-xl bg-violet-600 hover:bg-violet-500 active:bg-violet-700 text-white shadow-lg shadow-violet-600/20 transition-all disabled:opacity-50 disabled:cursor-not-allowed cursor-pointer flex-shrink-0"
        >
          <svg id="insights-btn-icon" class="w-3.5 h-3.5" fill="none" viewBox="0 0 24 24" stroke="currentColor">
            <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M4 4v5h.582m15.356 2A8.001 8.001 0 004.582 9m0 0H9m11 11v-5h-.581m0 0a8.003 8.003 0 01-15.357-2m15.357 2H15" />
          </svg>
          <svg id="insights-btn-spinner" class="hidden animate-spin w-3.5 h-3.5 text-white" fill="none" viewBox="0 0 24 24">
            <circle class="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" stroke-width="4"></circle>
            <path class="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4zm2 5.291A7.962 7.962 0 014 12H0c0 3.042 1.135 5.824 3 7.938l3-2.647z"></path>
          </svg>
          <span id="insights-btn-text">Generate Insights</span>
        </button>
      </div>

      <!-- Main Display Area -->
      <div id="insights-content-area" class="flex-1 flex flex-col justify-center min-h-[180px]">
        <!-- Initial Empty State -->
        <div id="insights-empty-state" class="text-center py-6 px-4 flex flex-col items-center">
          <div class="w-12 h-12 rounded-2xl bg-slate-800/80 border border-slate-700 flex items-center justify-center text-slate-400 mb-3">
            <svg class="w-6 h-6" fill="none" viewBox="0 0 24 24" stroke="currentColor">
              <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M9.663 17h4.673M12 3v1m6.364 1.636l-.707.707M21 12h-1M4 12H3m3.343-5.657l-.707-.707m2.828 9.9a5 5 0 117.072 0l-.548.547A3.374 3.374 0 0014 18.469V19a2 2 0 11-4 0v-.531c0-.895-.356-1.754-.988-2.386l-.548-.547z" />
            </svg>
          </div>
          <h3 class="text-sm font-semibold text-slate-200 mb-1">No Insights Generated Yet</h3>
          <p class="text-xs text-slate-400 max-w-sm mb-4">
            Click "Generate Insights" to run the Financial Advisor agent against your latest ingested transactions and budget metrics.
          </p>
          <button
            type="button"
            id="empty-state-trigger-btn"
            class="px-3.5 py-1.5 rounded-lg text-xs font-medium text-violet-400 bg-violet-950/50 hover:bg-violet-900/60 border border-violet-800/60 transition-colors cursor-pointer"
          >
            Run Financial Advisor
          </button>
        </div>

        <!-- Loading State Spinner -->
        <div id="insights-loading-state" class="hidden flex-col items-center justify-center py-10 px-4 text-center">
          <div class="relative flex items-center justify-center w-16 h-16 mb-4">
            <div class="absolute inset-0 rounded-full border-2 border-violet-500/20 animate-ping"></div>
            <div class="w-12 h-12 rounded-full border-2 border-violet-500/30 border-t-violet-400 animate-spin"></div>
            <svg class="w-5 h-5 text-violet-400 absolute" fill="none" viewBox="0 0 24 24" stroke="currentColor">
              <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M13 10V3L4 14h7v7l9-11h-7z" />
            </svg>
          </div>
          <h3 class="text-sm font-semibold text-white mb-1">AI CFO is Thinking...</h3>
          <p class="text-xs text-slate-400 max-w-xs animate-pulse">
            Analyzing transaction trends, calculating savings ratios, and generating strategic financial advice.
          </p>
        </div>

        <!-- Rendered Markdown Insights -->
        <div id="insights-result" class="hidden markdown-content animate-fade-in text-sm overflow-y-auto max-h-[420px] pr-2 mb-4">
        </div>
      </div>

      <!-- Multi-Turn Conversational Advisor Chat Section -->
      <div class="mt-4 pt-4 border-t border-slate-800">
        <div class="flex items-center justify-between mb-3">
          <div class="flex items-center gap-2">
            <span class="text-sm font-semibold text-white flex items-center gap-1.5">
              <span>💬</span> Multi-Turn Advisor Chat
            </span>
            <span class="px-2 py-0.5 text-[10px] font-semibold bg-violet-500/15 text-violet-300 rounded-full border border-violet-500/30">
              Stateless Vertex AI
            </span>
          </div>
          <button
            id="advisor-chat-clear-btn"
            type="button"
            class="text-[11px] text-slate-400 hover:text-rose-400 transition-colors hidden cursor-pointer"
            title="Clear chat history"
          >
            Clear Conversation
          </button>
        </div>

        <!-- Scrollable Chat History Container -->
        <div id="advisor-chat-history" class="space-y-3 mb-3 max-h-[320px] overflow-y-auto pr-1 text-xs">
          <div id="advisor-chat-starter" class="p-3.5 rounded-xl bg-slate-950/60 border border-slate-800 text-slate-400 space-y-2">
            <p>
              💡 <strong>Ask follow-up questions to your AI CFO!</strong> Conversation history is preserved across turns:
            </p>
            <div class="flex flex-wrap gap-1.5 pt-1">
              <button type="button" class="advisor-quick-prompt px-2.5 py-1 rounded-lg bg-slate-800 hover:bg-slate-700 text-slate-300 text-[11px] border border-slate-700 transition-colors cursor-pointer">
                How can I cut dining expenses?
              </button>
              <button type="button" class="advisor-quick-prompt px-2.5 py-1 rounded-lg bg-slate-800 hover:bg-slate-700 text-slate-300 text-[11px] border border-slate-700 transition-colors cursor-pointer">
                Can I increase my SIP to ₹25,000?
              </button>
              <button type="button" class="advisor-quick-prompt px-2.5 py-1 rounded-lg bg-slate-800 hover:bg-slate-700 text-slate-300 text-[11px] border border-slate-700 transition-colors cursor-pointer">
                Identify my biggest stealth spending leak
              </button>
            </div>
          </div>
        </div>

        <!-- Interactive Chat Form -->
        <form id="advisor-chat-form" class="flex items-center gap-2">
          <input
            id="advisor-chat-input"
            type="text"
            placeholder="Ask a question or follow-up advice (e.g. 'Can I afford a ₹15,000 trip?')..."
            class="flex-1 bg-slate-950 border border-slate-800 rounded-xl px-3.5 py-2.5 text-xs text-white placeholder-slate-500 focus:outline-none focus:border-violet-500 focus:ring-1 focus:ring-violet-500 transition-all"
            autocomplete="off"
          />
          <button
            id="advisor-chat-submit-btn"
            type="submit"
            class="px-4 py-2.5 rounded-xl bg-violet-600 hover:bg-violet-500 active:bg-violet-700 text-white text-xs font-semibold shadow-lg shadow-violet-600/20 transition-all cursor-pointer flex items-center gap-1.5 flex-shrink-0 disabled:opacity-50 disabled:cursor-not-allowed"
          >
            <span id="advisor-chat-btn-text">Send</span>
            <svg id="advisor-chat-send-icon" class="w-3.5 h-3.5" fill="none" viewBox="0 0 24 24" stroke="currentColor">
              <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M14 5l7 7m0 0l-7 7m7-7H3" />
            </svg>
            <svg id="advisor-chat-spinner" class="hidden animate-spin w-3.5 h-3.5 text-white" fill="none" viewBox="0 0 24 24">
              <circle class="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" stroke-width="4"></circle>
              <path class="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4z"></path>
            </svg>
          </button>
        </form>
      </div>
    </div>
  `;

  // Before the quick-prompt buttons get their click handlers below.
  if (RECORDED_DEMO) showRecordedQuestions(container);

  const fetchBtn = container.querySelector('#fetch-insights-btn');
  const emptyTriggerBtn = container.querySelector('#empty-state-trigger-btn');
  const btnIcon = container.querySelector('#insights-btn-icon');
  const btnSpinner = container.querySelector('#insights-btn-spinner');
  const btnText = container.querySelector('#insights-btn-text');
  const timestampEl = container.querySelector('#insights-timestamp');

  const emptyState = container.querySelector('#insights-empty-state');
  const loadingState = container.querySelector('#insights-loading-state');
  const resultContainer = container.querySelector('#insights-result');

  // Chat UI Elements
  const chatForm = container.querySelector('#advisor-chat-form');
  const chatInput = container.querySelector('#advisor-chat-input');
  const chatSubmitBtn = container.querySelector('#advisor-chat-submit-btn');
  const chatBtnText = container.querySelector('#advisor-chat-btn-text');
  const chatSendIcon = container.querySelector('#advisor-chat-send-icon');
  const chatSpinner = container.querySelector('#advisor-chat-spinner');
  const chatHistoryEl = container.querySelector('#advisor-chat-history');
  const chatClearBtn = container.querySelector('#advisor-chat-clear-btn');
  const starterEl = container.querySelector('#advisor-chat-starter');

  let isLoading = false;
  let isChatSending = false;

  // Frontend conversational state: list of { role: 'user' | 'model', content: string }
  let chatHistory = [];

  function setLoading(loading) {
    isLoading = loading;
    fetchBtn.disabled = loading;

    if (loading) {
      btnIcon.classList.add('hidden');
      btnSpinner.classList.remove('hidden');
      btnText.textContent = 'Analyzing...';
      emptyState.classList.add('hidden');
      resultContainer.classList.add('hidden');
      loadingState.classList.remove('hidden');
      loadingState.classList.add('flex');
    } else {
      btnIcon.classList.remove('hidden');
      btnSpinner.classList.add('hidden');
      btnText.textContent = 'Refresh Insights';
      loadingState.classList.add('hidden');
      loadingState.classList.remove('flex');
    }
  }

  function setChatLoading(loading) {
    isChatSending = loading;
    chatSubmitBtn.disabled = loading;
    chatInput.disabled = loading;

    if (loading) {
      chatSendIcon.classList.add('hidden');
      chatSpinner.classList.remove('hidden');
      chatBtnText.textContent = 'Thinking...';
    } else {
      chatSendIcon.classList.remove('hidden');
      chatSpinner.classList.add('hidden');
      chatBtnText.textContent = 'Send';
      chatInput.focus();
    }
  }

  function appendChatMessage(role, content) {
    if (starterEl) {
      starterEl.classList.add('hidden');
    }
    chatClearBtn.classList.remove('hidden');

    const msgEl = document.createElement('div');
    msgEl.className = 'flex flex-col gap-1';

    if (role === 'user') {
      msgEl.innerHTML = `
        <div class="flex items-end justify-end gap-2">
          <div class="max-w-[85%] bg-violet-600 text-white px-3.5 py-2 rounded-2xl rounded-tr-sm shadow-md text-xs leading-relaxed">
            ${escapeHtml(content)}
          </div>
          <div class="w-6 h-6 rounded-full bg-slate-800 border border-slate-700 flex items-center justify-center text-[10px] text-slate-300 font-bold flex-shrink-0">
            You
          </div>
        </div>
      `;
    } else {
      const parsedHtml = renderMarkdown(content);
      msgEl.innerHTML = `
        <div class="flex items-start gap-2">
          <div class="w-6 h-6 rounded-full bg-violet-950/80 border border-violet-500/40 text-violet-300 flex items-center justify-center text-[10px] font-bold flex-shrink-0 mt-0.5">
            CFO
          </div>
          <div class="max-w-[90%] bg-slate-950/80 border border-slate-800 text-slate-200 px-3.5 py-2.5 rounded-2xl rounded-tl-sm shadow-md text-xs leading-relaxed markdown-content">
            ${parsedHtml}
          </div>
        </div>
      `;
    }

    chatHistoryEl.appendChild(msgEl);
    chatHistoryEl.scrollTop = chatHistoryEl.scrollHeight;
  }

  async function handleSendChatMessage(text) {
    const userMessage = (text || chatInput.value || '').trim();
    if (!userMessage || isChatSending) return;

    // 1. Record history snapshot before this user message to send as past context
    const priorHistory = [...chatHistory];

    // 2. Append User Message to UI and memory
    chatInput.value = '';
    appendChatMessage('user', userMessage);
    chatHistory.push({ role: 'user', content: userMessage });

    try {
      setChatLoading(true);

      // 3. Send stateless request to backend with message + prior history
      const response = await sendAdvisorChatMessage(userMessage, priorHistory);
      const replyText = response.reply || 'I was unable to process that financial query.';

      // 4. Append Advisor Response to UI and memory
      appendChatMessage('model', replyText);
      chatHistory.push({ role: 'model', content: replyText });
    } catch (err) {
      console.error('[Insights] Chat error:', err);
      toast.show(err.message || 'Failed to reach AI CFO Advisor.', 'error', 5000);
      appendChatMessage('model', `⚠️ **Error:** ${err.message || 'Could not reach the AI Financial Advisor. Please check network connection.'}`);
    } finally {
      setChatLoading(false);
    }
  }

  async function handleFetchInsights() {
    if (isLoading) return;

    try {
      setLoading(true);

      const data = await getAiInsights();
      const rawMarkdown = data.insights || '';

      if (!rawMarkdown.trim()) {
        resultContainer.innerHTML = `
          <div class="p-4 rounded-xl bg-slate-800/40 border border-slate-700 text-slate-300 text-xs">
            No insights available. Upload bank transactions to give your AI CFO data to analyze.
          </div>
        `;
      } else {
        // Parse markdown with marked and sanitize it with DOMPurify
        resultContainer.innerHTML = renderMarkdown(rawMarkdown);
      }

      resultContainer.classList.remove('hidden');
      timestampEl.textContent = `Updated ${new Date().toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })}`;
      toast.show('AI Financial Advisor insights generated!', 'success', 3000);
    } catch (error) {
      toast.show(error.message || 'Failed to fetch insights from AI Advisor.', 'error', 6000);
      emptyState.classList.remove('hidden');
      resultContainer.classList.add('hidden');
    } finally {
      setLoading(false);
    }
  }

  // Event Listeners
  fetchBtn.addEventListener('click', handleFetchInsights);
  emptyTriggerBtn.addEventListener('click', handleFetchInsights);

  chatForm.addEventListener('submit', (e) => {
    e.preventDefault();
    handleSendChatMessage();
  });

  // Quick prompt buttons
  container.querySelectorAll('.advisor-quick-prompt').forEach((btn) => {
    btn.addEventListener('click', () => {
      handleSendChatMessage(btn.textContent.trim());
    });
  });

  // Clear chat history
  chatClearBtn.addEventListener('click', () => {
    chatHistory = [];
    chatHistoryEl.innerHTML = '';
    if (starterEl) {
      chatHistoryEl.appendChild(starterEl);
      starterEl.classList.remove('hidden');
    }
    chatClearBtn.classList.add('hidden');
    toast.show('Conversation history cleared.', 'info', 2000);
  });
}


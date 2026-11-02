import {
  Bot,
  CheckCircle2,
  Mic,
  MicOff,
  Send,
  Sparkles,
  Volume2,
  VolumeX,
  X,
} from 'lucide-react';
import { useCallback, useEffect, useRef, useState, type SyntheticEvent } from 'react';
import { sendJarvisCommand } from '../api/agents';
import { recordedCopilotPrompts } from '../recorded/manifest';
import { RECORDED_DEMO } from '../recorded/mode';
import { buttonClass, inputClass } from '../ui/classes';
import { Spinner } from '../ui/Spinner';
import type { ScreenId } from './screens';

interface CopilotTurn {
  id: string;
  role: 'user' | 'assistant';
  content: string;
  spokenContent?: string;
  agentsInvoked?: string[];
  ledgerMutated?: boolean;
  timestamp: string;
}

interface SpeechRecognitionAlternativeLike {
  transcript: string;
}

interface SpeechRecognitionResultLike {
  isFinal: boolean;
  0: SpeechRecognitionAlternativeLike;
}

interface SpeechRecognitionEventLike {
  results: ArrayLike<SpeechRecognitionResultLike>;
}

interface SpeechRecognitionLike {
  lang: string;
  interimResults: boolean;
  continuous: boolean;
  onresult: ((event: SpeechRecognitionEventLike) => void) | null;
  onerror: ((event: unknown) => void) | null;
  onend: (() => void) | null;
  start: () => void;
  stop: () => void;
}

type SpeechRecognitionConstructor = new () => SpeechRecognitionLike;

function getSpeechRecognitionCtor(): SpeechRecognitionConstructor | null {
  if (typeof window === 'undefined') return null;
  const win = window as unknown as {
    SpeechRecognition?: SpeechRecognitionConstructor;
    webkitSpeechRecognition?: SpeechRecognitionConstructor;
  };
  return win.SpeechRecognition ?? win.webkitSpeechRecognition ?? null;
}

interface MasterCopilotDockProps {
  open: boolean;
  onToggleOpen: (nextOpen: boolean) => void;
  screenId: ScreenId | null;
  screenLabel: string;
  month: string;
  searchQuery: string;
  onLedgerMutated: () => void;
}

export function MasterCopilotDock({
  open,
  onToggleOpen,
  screenId,
  screenLabel,
  month,
  searchQuery,
  onLedgerMutated,
}: MasterCopilotDockProps) {
  const [turns, setTurns] = useState<CopilotTurn[]>([
    {
      id: 'welcome',
      role: 'assistant',
      content:
        'Hello! I am **Jarvis**, your Master CFO Orchestrator. I coordinate all 4 specialist agents (**Ingestion**, **Reconciliation**, **Clarification**, and **Advisor**) across every page.\n\n' +
        'You can ask me by **Voice** or **Text** to:\n' +
        '- Walk through the numbers on your current page (`' +
        screenLabel +
        '`)\n' +
        '- Change any transaction category (e.g. *"Change #1259 to Personal"* or *"Categorize KPN Farm Fresh as Food"*)\n' +
        '- Audit & resolve ambiguous categories across your ledger\n' +
        '- Verify your Kotak + HDFC bank balance math or true monthly salary (~₹50.5k/mo vs 19-month cumulative)',
      agentsInvoked: ['orchestrator', 'ingestion-agent', 'reconciliation-agent', 'clarification-agent', 'advisor-agent'],
      timestamp: 'Online',
    },
  ]);
  const [input, setInput] = useState('');
  const [busy, setBusy] = useState(false);
  const [voiceSpeakEnabled, setVoiceSpeakEnabled] = useState(true);
  const [speaking, setSpeaking] = useState(false);
  const [listening, setListening] = useState(false);
  const [voiceNotice, setVoiceNotice] = useState<string | null>(null);

  const recognitionRef = useRef<SpeechRecognitionLike | null>(null);
  const scrollRef = useRef<HTMLDivElement>(null);

  const stopSpeaking = useCallback(() => {
    if (typeof window !== 'undefined' && 'speechSynthesis' in window) {
      window.speechSynthesis.cancel();
    }
    setSpeaking(false);
  }, []);

  const speakText = useCallback(
    (text: string) => {
      if (!voiceSpeakEnabled || typeof window === 'undefined' || !('speechSynthesis' in window)) return;
      try {
        window.speechSynthesis.cancel();
        const utter = new SpeechSynthesisUtterance(text);
        utter.lang = 'en-IN';
        utter.rate = 1.03;
        const voices = window.speechSynthesis.getVoices();
        const preferred =
          voices.find((v) => v.lang.includes('en-IN') || v.name.toLowerCase().includes('india')) ??
          voices.find((v) => v.lang.startsWith('en') && v.name.toLowerCase().includes('natural')) ??
          voices.find((v) => v.lang.startsWith('en'));
        if (preferred) utter.voice = preferred;
        utter.onstart = () => {
          setSpeaking(true);
        };
        utter.onend = () => {
          setSpeaking(false);
        };
        utter.onerror = () => {
          setSpeaking(false);
        };
        window.speechSynthesis.speak(utter);
      } catch {
        setSpeaking(false);
      }
    },
    [voiceSpeakEnabled],
  );

  useEffect(() => {
    if (scrollRef.current) {
      scrollRef.current.scrollTop = scrollRef.current.scrollHeight;
    }
  }, [turns, open]);

  useEffect(() => {
    return () => {
      stopSpeaking();
      recognitionRef.current?.stop();
    };
  }, [stopSpeaking]);

  const dispatchCommand = useCallback(
    async (rawMessage: string, speakResponse = false) => {
      const trimmed = rawMessage.trim();
      if (!trimmed || busy) return;
      onToggleOpen(true);
      setVoiceNotice(null);

      const userTurn: CopilotTurn = {
        id: `u-${String(Date.now())}`,
        role: 'user',
        content: trimmed,
        timestamp: new Date().toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' }),
      };
      setTurns((prev) => [...prev, userTurn]);
      setInput('');
      setBusy(true);

      try {
        const historyPayload = turns.slice(-6).map((t) => ({
          role: t.role,
          content: t.content,
        }));
        const res = await sendJarvisCommand({
          message: trimmed,
          current_screen: screenId ?? 'transactions',
          current_month: month || 'ALL',
          search_query: searchQuery,
          history: historyPayload,
          auto_execute: true,
        });

        const assistantTurn: CopilotTurn = {
          id: `a-${String(Date.now())}`,
          role: 'assistant',
          content: res.reply,
          spokenContent: res.spoken_reply,
          agentsInvoked: res.agents_invoked,
          ledgerMutated: res.ledger_mutated,
          timestamp: new Date().toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' }),
        };
        setTurns((prev) => [...prev, assistantTurn]);

        if (res.ledger_mutated) {
          onLedgerMutated();
        }
        if (speakResponse || voiceSpeakEnabled) {
          speakText(res.spoken_reply);
        }
      } catch (err) {
        const errMsg = err instanceof Error ? err.message : 'Master Agent command failed.';
        setTurns((prev) => [
          ...prev,
          {
            id: `e-${String(Date.now())}`,
            role: 'assistant',
            content: `⚠️ ${errMsg}`,
            timestamp: new Date().toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' }),
          },
        ]);
      } finally {
        setBusy(false);
      }
    },
    [busy, month, onLedgerMutated, onToggleOpen, screenId, searchQuery, speakText, turns, voiceSpeakEnabled],
  );

  const toggleVoiceListening = useCallback(() => {
    if (listening) {
      recognitionRef.current?.stop();
      setListening(false);
      return;
    }

    const Ctor = getSpeechRecognitionCtor();
    if (!Ctor) {
      onToggleOpen(true);
      setVoiceNotice('Browser speech recognition is unavailable in this tab — type your command below and Jarvis will speak the reply aloud.');
      return;
    }

    stopSpeaking();
    onToggleOpen(true);
    setVoiceNotice('Listening… Speak your question or category update command.');
    const recognition = new Ctor();
    recognition.lang = 'en-IN';
    recognition.interimResults = true;
    recognition.continuous = false;
    recognitionRef.current = recognition;

    let finalTranscript = '';
    recognition.onresult = (event: SpeechRecognitionEventLike) => {
      let interim = '';
      for (const r of Array.from(event.results)) {
        if (r.isFinal) {
          finalTranscript += r[0].transcript;
        } else {
          interim += r[0].transcript;
        }
      }
      setInput((finalTranscript || interim).trim());
    };

    recognition.onerror = () => {
      setListening(false);
      setVoiceNotice('Could not capture microphone audio. Check microphone permissions or type below.');
    };

    recognition.onend = () => {
      setListening(false);
      const captured = finalTranscript.trim();
      if (captured) {
        setVoiceNotice(null);
        void dispatchCommand(captured, true);
      }
    };

    setListening(true);
    recognition.start();
  }, [dispatchCommand, listening, onToggleOpen, stopSpeaking]);

  const handleSubmit = (event: SyntheticEvent<HTMLFormElement>) => {
    event.preventDefault();
    void dispatchCommand(input, false);
  };

  const periodBadge = month && month !== 'ALL' ? month : 'All Months (19m Avg)';

  // The recorded demo offers the commands it can replay (src/recorded/transport.ts).
  const quickPrompts = RECORDED_DEMO
    ? recordedCopilotPrompts()
    : [
        `Walk me through ${screenLabel} (${periodBadge})`,
        'What is my true monthly salary & surplus?',
        'Audit & resolve categories',
        'Verify bank balance math',
      ];

  return (
    <>
      {/* Always-On Floating Jarvis Pill (Bottom-Right across all screens) */}
      <div
        className="fixed bottom-5 right-5 z-40 flex items-center gap-2"
        data-testid="jarvis-floating-dock"
      >
        <button
          type="button"
          onClick={toggleVoiceListening}
          className={`inline-flex h-11 w-11 items-center justify-center rounded-full border shadow-lg transition focus-visible:outline focus-visible:outline-2 focus-visible:outline-focus ${
            listening
              ? 'animate-pulse border-danger bg-danger text-white'
              : 'border-line bg-surface text-accent-text hover:bg-accent-soft'
          }`}
          title={listening ? 'Stop listening' : 'Talk to Jarvis (Voice Command)'}
          aria-label={listening ? 'Stop voice input' : 'Talk to Jarvis Master Agent'}
          data-testid="jarvis-floating-mic-btn"
        >
          {listening ? <MicOff aria-hidden="true" className="h-5 w-5" /> : <Mic aria-hidden="true" className="h-5 w-5" />}
        </button>

        <button
          type="button"
          onClick={() => {
            onToggleOpen(!open);
          }}
          className="inline-flex items-center gap-2 rounded-full border border-line bg-accent px-4 py-2.5 text-sm font-semibold text-accent-fg shadow-lg transition hover:bg-accent-hover focus-visible:outline focus-visible:outline-2 focus-visible:outline-focus"
          data-testid="jarvis-toggle-btn"
        >
          <Sparkles aria-hidden="true" className="h-4 w-4" />
          <span>Jarvis · Master Agent</span>
          <span className="hidden rounded-full bg-black/20 px-2 py-0.5 font-mono text-[11px] sm:inline">
            {screenLabel}
          </span>
        </button>
      </div>

      {/* Slide-Up / Floating Jarvis Master Orchestrator HUD Panel */}
      {open ? (
        <aside
          aria-label="Jarvis Master CFO Orchestrator"
          className="fixed bottom-20 right-4 z-40 flex h-[min(640px,calc(100dvh-6.5rem))] w-[min(460px,calc(100vw-2rem))] flex-col overflow-hidden rounded-2xl border border-line bg-surface shadow-2xl"
          data-testid="jarvis-copilot-panel"
        >
          {/* Header */}
          <div className="border-b border-line bg-surface-2 px-4 py-3">
            <div className="flex items-center justify-between gap-2">
              <div className="flex items-center gap-2">
                <span className="inline-flex h-8 w-8 items-center justify-center rounded-lg bg-accent-soft text-accent-text">
                  <Bot aria-hidden="true" className="h-4 w-4" />
                </span>
                <div>
                  <div className="flex items-center gap-1.5">
                    <h2 className="text-sm font-bold text-fg">Jarvis · Master CFO Orchestrator</h2>
                    <span className="inline-flex items-center rounded-full bg-success-soft px-2 py-0.5 text-[10px] font-semibold text-success">
                      5 Agents Live
                    </span>
                  </div>
                  <p className="font-mono text-[11px] text-fg-muted">
                    Page: <strong className="text-fg">{screenLabel}</strong> · Period:{' '}
                    <strong className="text-fg">{periodBadge}</strong>
                  </p>
                </div>
              </div>

              <div className="flex items-center gap-1">
                <button
                  type="button"
                  onClick={() => {
                    if (speaking) {
                      stopSpeaking();
                    }
                    setVoiceSpeakEnabled((prev) => !prev);
                  }}
                  className={buttonClass('ghost', 'sm')}
                  title={voiceSpeakEnabled ? 'Voice replies ON (click to mute)' : 'Voice replies OFF (click to enable)'}
                  aria-label={voiceSpeakEnabled ? 'Mute Jarvis voice replies' : 'Enable Jarvis voice replies'}
                >
                  {voiceSpeakEnabled ? (
                    <Volume2 aria-hidden="true" className="h-4 w-4 text-accent-text" />
                  ) : (
                    <VolumeX aria-hidden="true" className="h-4 w-4 text-fg-muted" />
                  )}
                </button>
                <button
                  type="button"
                  onClick={() => {
                    stopSpeaking();
                    onToggleOpen(false);
                  }}
                  className={buttonClass('ghost', 'sm')}
                  aria-label="Close Jarvis panel"
                >
                  <X aria-hidden="true" className="h-4 w-4" />
                </button>
              </div>
            </div>

            {/* Context-Aware Quick Action Chips */}
            <div className="mt-2.5 flex flex-wrap gap-1.5">
              {quickPrompts.map((prompt) => (
                <button
                  key={prompt}
                  type="button"
                  disabled={busy}
                  onClick={() => {
                    void dispatchCommand(prompt, false);
                  }}
                  className="rounded-full border border-line bg-surface px-2.5 py-1 text-[11px] font-medium text-fg transition hover:border-accent-text hover:bg-accent-soft hover:text-accent-text disabled:opacity-50"
                >
                  {prompt}
                </button>
              ))}
            </div>
          </div>

          {voiceNotice ? (
            <div className="border-b border-line bg-accent-soft px-4 py-2 text-xs font-medium text-accent-text">
              {voiceNotice}
            </div>
          ) : null}

          {/* Conversation Stream */}
          <div ref={scrollRef} className="flex-1 space-y-3 overflow-y-auto p-4 text-xs">
            {turns.map((turn) => (
              <div
                key={turn.id}
                className={`flex flex-col ${turn.role === 'user' ? 'items-end' : 'items-start'}`}
              >
                <div
                  className={`max-w-[92%] rounded-xl px-3.5 py-2.5 ${
                    turn.role === 'user'
                      ? 'bg-accent text-accent-fg'
                      : 'border border-line bg-surface-2 text-fg'
                  }`}
                >
                  <div className="whitespace-pre-wrap leading-relaxed">{turn.content}</div>

                  {turn.agentsInvoked && turn.agentsInvoked.length > 0 ? (
                    <div className="mt-2 flex flex-wrap items-center gap-1 border-t border-line pt-1.5">
                      {turn.agentsInvoked.map((ag) => (
                        <span
                          key={ag}
                          className="rounded bg-surface px-1.5 py-0.5 font-mono text-[10px] text-fg-muted"
                        >
                          {ag}
                        </span>
                      ))}
                      {turn.ledgerMutated ? (
                        <span className="inline-flex items-center gap-1 rounded bg-success-soft px-1.5 py-0.5 font-mono text-[10px] font-semibold text-success">
                          <CheckCircle2 aria-hidden="true" className="h-3 w-3" />
                          Ledger Updated Live
                        </span>
                      ) : null}
                      {turn.spokenContent ? (
                        <button
                          type="button"
                          onClick={() => {
                            speakText(turn.spokenContent ?? '');
                          }}
                          className="ml-auto inline-flex items-center gap-1 rounded px-1.5 py-0.5 text-[10px] font-semibold text-accent-text hover:bg-accent-soft"
                        >
                          <Volume2 aria-hidden="true" className="h-3 w-3" />
                          Speak
                        </button>
                      ) : null}
                    </div>
                  ) : null}
                </div>
                <span className="mt-0.5 px-1 text-[10px] text-fg-muted">{turn.timestamp}</span>
              </div>
            ))}
            {busy ? (
              <div className="flex items-center gap-2 rounded-xl border border-line bg-surface-2 px-3.5 py-2.5 text-xs text-fg-muted">
                <Spinner /> Jarvis Orchestrator is delegating across specialist agents…
              </div>
            ) : null}
          </div>

          {/* Voice + Text Input Bar */}
          <form onSubmit={handleSubmit} className="border-t border-line bg-surface-2 p-3">
            <div className="flex items-center gap-2">
              <button
                type="button"
                onClick={toggleVoiceListening}
                className={
                  listening
                    ? 'inline-flex h-8 items-center justify-center rounded-lg bg-danger px-2.5 text-xs font-medium text-white shadow-sm animate-pulse'
                    : buttonClass('secondary', 'sm')
                }
                title={listening ? 'Stop listening' : 'Speak command to Jarvis'}
                aria-label={listening ? 'Stop voice listening' : 'Start voice listening'}
                data-testid="jarvis-panel-mic-btn"
              >
                {listening ? <MicOff aria-hidden="true" className="h-4 w-4" /> : <Mic aria-hidden="true" className="h-4 w-4" />}
              </button>
              <input
                type="text"
                value={input}
                onChange={(e) => {
                  setInput(e.target.value);
                }}
                placeholder='Ask anything or say "Change #1259 to Personal"…'
                className={`${inputClass} h-9 flex-1 text-xs`}
                data-testid="jarvis-command-input"
              />
              <button
                type="submit"
                disabled={busy || !input.trim()}
                className={buttonClass('primary', 'sm')}
                aria-label="Send command to Jarvis"
                data-testid="jarvis-send-btn"
              >
                <Send aria-hidden="true" className="h-4 w-4" />
              </button>
            </div>
          </form>
        </aside>
      ) : null}
    </>
  );
}

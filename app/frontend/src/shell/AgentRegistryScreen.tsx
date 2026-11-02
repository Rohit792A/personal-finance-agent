import {
  Activity,
  Bot,
  BrainCircuit,
  CheckCircle2,
  Cpu,
  Play,
  Plus,
  RefreshCw,
  ShieldCheck,
  Sparkles,
  Trash2,
  Wrench,
} from 'lucide-react';
import { useCallback, useEffect, useState, type SyntheticEvent } from 'react';
import {
  applyMasterCategoryResolutions,
  createLearnedRule,
  deleteLearnedRule,
  executeMcpTool,
  getAgentAuditTrail,
  getAgentRegistry,
  getLearnedRules,
  getMcpTools,
  getWeakestTelemetryStep,
  invokeAgentSkill,
  runFourAgentVerification,
  runMasterCategoryResolution,
  type AgentRegistryResponse,
  type AuditLogItem,
  type LearnedRuleItem,
  type MasterResolutionDecision,
  type MasterResolutionReport,
  type McpCatalogueResponse,
  type WeakestStepInfo,
  type WorkflowVerificationResult,
} from '../api/agents';
import { buttonClass, inputClass } from '../ui/classes';
import { Spinner } from '../ui/Spinner';

const CATEGORIES = [
  'Rent',
  'Food',
  'SIP',
  'Utilities',
  'Transportation',
  'Health/Medical',
  'Home',
  'Personal',
  'Travel',
  'Debt',
  'Investment',
  'Stocks',
  'Savings',
  'Paycheck',
  'Bonus',
  'Interest',
  'Transfer',
  'Other',
] as const;

const TX_TYPES = ['EXPENSE', 'INCOME', 'TRANSFER'] as const;

export function AgentRegistryScreen() {
  const [registry, setRegistry] = useState<AgentRegistryResponse | null>(null);
  const [rules, setRules] = useState<LearnedRuleItem[]>([]);
  const [mcp, setMcp] = useState<McpCatalogueResponse | null>(null);
  const [auditLogs, setAuditLogs] = useState<AuditLogItem[]>([]);
  const [weakestStep, setWeakestStep] = useState<WeakestStepInfo | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  // Rule creation form state
  const [patternInput, setPatternInput] = useState('');
  const [categoryInput, setCategoryInput] = useState<string>('Rent');
  const [ruleBusy, setRuleBusy] = useState(false);
  const [ruleNotice, setRuleNotice] = useState<string | null>(null);

  // Live 4-Agent workflow verification state
  const [verifying, setVerifying] = useState(false);
  const [verification, setVerification] = useState<WorkflowVerificationResult | null>(null);

  // Master Agent Category Resolution state
  const [masterBusy, setMasterBusy] = useState(false);
  const [masterReport, setMasterReport] = useState<MasterResolutionReport | null>(null);
  const [editedDecisions, setEditedDecisions] = useState<Record<number, { category: string; type: string }>>({});
  const [appliedRowIds, setAppliedRowIds] = useState<Record<number, boolean>>({});
  const [masterNotice, setMasterNotice] = useState<string | null>(null);

  // Direct A2A Skill & MCP Tool invocation state
  const [skillBusyKey, setSkillBusyKey] = useState<string | null>(null);
  const [agentSkillOutputs, setAgentSkillOutputs] = useState<Record<string, { skillId: string; output: unknown }>>({});
  const [mcpBusyTool, setMcpBusyTool] = useState<string | null>(null);
  const [mcpOutputs, setMcpOutputs] = useState<Record<string, unknown>>({});

  const loadAll = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const [regData, rulesData, mcpData, auditData, weakestData] = await Promise.all([
        getAgentRegistry(),
        getLearnedRules(),
        getMcpTools(),
        getAgentAuditTrail(20),
        getWeakestTelemetryStep(),
      ]);
      setRegistry(regData);
      setRules(rulesData);
      setMcp(mcpData);
      setAuditLogs(auditData);
      setWeakestStep(weakestData);
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Could not load Agent Registry status.');
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    let cancelled = false;
    Promise.all([
      getAgentRegistry(),
      getLearnedRules(),
      getMcpTools(),
      getAgentAuditTrail(20),
      getWeakestTelemetryStep(),
    ]).then(
      ([regData, rulesData, mcpData, auditData, weakestData]) => {
        if (cancelled) return;
        setRegistry(regData);
        setRules(rulesData);
        setMcp(mcpData);
        setAuditLogs(auditData);
        setWeakestStep(weakestData);
        setLoading(false);
      },
      (err: unknown) => {
        if (cancelled) return;
        setError(err instanceof Error ? err.message : 'Could not load Agent Registry status.');
        setLoading(false);
      },
    );
    return () => {
      cancelled = true;
    };
  }, []);

  const handleTeachRule = async (event: SyntheticEvent<HTMLFormElement>) => {
    event.preventDefault();
    const trimmed = patternInput.trim();
    if (!trimmed) return;
    setRuleBusy(true);
    setRuleNotice(null);
    try {
      const created = await createLearnedRule(trimmed, categoryInput);
      setPatternInput('');
      setRuleNotice(
        `Clarification Agent learned rule: "${created.payee_pattern}" → ${created.resolved_category} (retroactively applied to matching unverified rows).`,
      );
      const [updatedRules, updatedAudit] = await Promise.all([getLearnedRules(), getAgentAuditTrail(20)]);
      setRules(updatedRules);
      setAuditLogs(updatedAudit);
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to save learned rule.');
    } finally {
      setRuleBusy(false);
    }
  };

  const handleDeleteRule = async (pattern: string) => {
    try {
      await deleteLearnedRule(pattern);
      setRules((prev) => prev.filter((item) => item.payee_pattern !== pattern));
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to remove rule.');
    }
  };

  const handleRunWorkflow = async () => {
    setVerifying(true);
    setError(null);
    try {
      const result = await runFourAgentVerification();
      setVerification(result);
      const [updatedAudit, updatedWeakest, updatedRules] = await Promise.all([
        getAgentAuditTrail(20),
        getWeakestTelemetryStep(),
        getLearnedRules(),
      ]);
      setAuditLogs(updatedAudit);
      setWeakestStep(updatedWeakest);
      setRules(updatedRules);
    } catch (err) {
      setError(err instanceof Error ? err.message : '4-Agent verification run failed.');
    } finally {
      setVerifying(false);
    }
  };

  const handleRunMasterResolution = async (autoApply: boolean) => {
    setMasterBusy(true);
    setError(null);
    setMasterNotice(null);
    try {
      const report = await runMasterCategoryResolution({
        month: 'ALL',
        include_locked_other: true,
        auto_apply: autoApply,
      });
      setMasterReport(report);
      const nextEdits: Record<number, { category: string; type: string }> = {};
      const nextApplied: Record<number, boolean> = {};
      for (const d of report.decisions) {
        nextEdits[d.id] = {
          category: d.recommended_category,
          type: d.recommended_type,
        };
        if (autoApply) {
          nextApplied[d.id] = true;
        }
      }
      setEditedDecisions(nextEdits);
      setAppliedRowIds(nextApplied);
      if (autoApply && report.applied_result) {
        setMasterNotice(
          `Master Agent updated ${String(report.applied_result.updated_count)} ledger rows and taught ${String(report.applied_result.rules_learned_count)} reusable payee rules.`,
        );
      } else {
        setMasterNotice(
          `Master Agent analyzed ${String(report.total_candidates)} candidate transactions across all 4 specialist agents. Review the evidence-backed decisions below or click "Commit All Decisions & Teach Rules".`,
        );
      }
      const [updatedRules, updatedAudit, updatedWeakest] = await Promise.all([
        getLearnedRules(),
        getAgentAuditTrail(20),
        getWeakestTelemetryStep(),
      ]);
      setRules(updatedRules);
      setAuditLogs(updatedAudit);
      setWeakestStep(updatedWeakest);
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Master Agent category resolution failed.');
    } finally {
      setMasterBusy(false);
    }
  };

  const handleApplySingleDecision = async (decision: MasterResolutionDecision) => {
    setMasterBusy(true);
    setError(null);
    try {
      const override = editedDecisions[decision.id];
      const targetCategory = override?.category ?? decision.recommended_category;
      const targetType = override?.type ?? decision.recommended_type;
      const res = await applyMasterCategoryResolutions([
        {
          id: decision.id,
          category: targetCategory,
          transaction_type: targetType,
          is_self_transfer: targetType === 'TRANSFER' || targetCategory === 'Transfer',
          payee_pattern: decision.payee_pattern,
          teach_rule: decision.teach_rule,
          reasoning: decision.reasoning,
        },
      ]);
      setAppliedRowIds((prev) => ({ ...prev, [decision.id]: true }));
      setMasterNotice(
        `Updated Transaction #${String(decision.id)} → ${targetType}:${targetCategory} (${String(res.rules_learned_count)} rule learned).`,
      );
      const [updatedRules, updatedAudit] = await Promise.all([getLearnedRules(), getAgentAuditTrail(20)]);
      setRules(updatedRules);
      setAuditLogs(updatedAudit);
    } catch (err) {
      setError(err instanceof Error ? err.message : `Failed to update transaction #${String(decision.id)}.`);
    } finally {
      setMasterBusy(false);
    }
  };

  const handleApplyAllDecisions = async () => {
    if (!masterReport || masterReport.decisions.length === 0) return;
    setMasterBusy(true);
    setError(null);
    try {
      const payload = masterReport.decisions.map((d) => {
        const override = editedDecisions[d.id];
        const targetCategory = override?.category ?? d.recommended_category;
        const targetType = override?.type ?? d.recommended_type;
        return {
          id: d.id,
          category: targetCategory,
          transaction_type: targetType,
          is_self_transfer: targetType === 'TRANSFER' || targetCategory === 'Transfer',
          payee_pattern: d.payee_pattern,
          teach_rule: d.teach_rule,
          reasoning: d.reasoning,
        };
      });
      const res = await applyMasterCategoryResolutions(payload);
      const nextApplied: Record<number, boolean> = {};
      for (const item of payload) {
        nextApplied[item.id] = true;
      }
      setAppliedRowIds(nextApplied);
      setMasterNotice(
        `Committed ${String(res.updated_count)} transaction updates to PostgreSQL and persisted ${String(res.rules_learned_count)} payee rules in learned_rules.`,
      );
      const [updatedRules, updatedAudit] = await Promise.all([getLearnedRules(), getAgentAuditTrail(20)]);
      setRules(updatedRules);
      setAuditLogs(updatedAudit);
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to commit Master Agent decisions.');
    } finally {
      setMasterBusy(false);
    }
  };

  const handleInvokeAgentSkill = async (agentId: string, skillId: string) => {
    const busyKey = `${agentId}:${skillId}`;
    setSkillBusyKey(busyKey);
    setError(null);
    try {
      if (skillId === 'MASTER_RESOLVE_TRANSACTIONS') {
        await handleRunMasterResolution(false);
      }
      const res = await invokeAgentSkill(agentId, skillId);
      setAgentSkillOutputs((prev) => ({
        ...prev,
        [agentId]: { skillId, output: res.result ?? res },
      }));
      const updatedAudit = await getAgentAuditTrail(20);
      setAuditLogs(updatedAudit);
    } catch (err) {
      setError(err instanceof Error ? err.message : `Skill invocation failed (${skillId}).`);
    } finally {
      setSkillBusyKey(null);
    }
  };

  const handleRunMcpTool = async (toolName: string) => {
    setMcpBusyTool(toolName);
    setError(null);
    try {
      const res = await executeMcpTool(toolName);
      setMcpOutputs((prev) => ({
        ...prev,
        [toolName]: res.data ?? res,
      }));
      const updatedAudit = await getAgentAuditTrail(20);
      setAuditLogs(updatedAudit);
    } catch (err) {
      setError(err instanceof Error ? err.message : `MCP Tool ${toolName} failed.`);
    } finally {
      setMcpBusyTool(null);
    }
  };

  return (
    <div className="space-y-6" data-testid="agent-registry-screen">
      {/* Header Banner */}
      <section className="rounded-xl border border-line bg-surface p-6 shadow-sm">
        <div className="flex flex-col gap-4 lg:flex-row lg:items-center lg:justify-between">
          <div>
            <div className="flex flex-wrap items-center gap-2">
              <span className="inline-flex items-center gap-1.5 rounded-full bg-accent-soft px-3 py-1 text-xs font-semibold text-accent-text">
                <Bot aria-hidden="true" className="h-3.5 w-3.5" />
                Google Cloud Agent Registry · A2A v0.3.0
              </span>
              <span className="inline-flex items-center gap-1.5 rounded-full bg-success-soft px-3 py-1 text-xs font-semibold text-success">
                <CheckCircle2 aria-hidden="true" className="h-3.5 w-3.5" />
                {registry?.registry_status ?? 'ONLINE'}
              </span>
            </div>
            <h2 className="mt-3 text-xl font-bold text-fg">
              4-Agent CFO Fleet &amp; Closed-Loop Categorization Registry
            </h2>
            <p className="mt-1 text-sm text-fg-muted">
              Provisioned on <code className="font-mono text-fg">{registry?.gcp_agent_registry_api ?? 'agentregistry.googleapis.com/v1alpha'}</code>{' '}
              in project <code className="font-mono text-fg">{registry?.gcp_project_id ?? 'personal-finance-agent-507010'}</code>{' '}
              with SPIFFE workload identity and Official MCP tool bindings.
            </p>
          </div>
          <div className="flex flex-wrap items-center gap-2">
            <button
              type="button"
              onClick={() => {
                void handleRunWorkflow();
              }}
              disabled={verifying || loading}
              className={buttonClass('primary', 'sm')}
              data-testid="run-4agent-workflow-btn"
            >
              <Play aria-hidden="true" className="h-4 w-4" />
              {verifying ? 'Running 4-Agent Pipeline…' : 'Verify 4-Agent Pipeline'}
            </button>
            <button
              type="button"
              onClick={() => {
                void loadAll();
              }}
              disabled={loading}
              className={buttonClass('secondary', 'sm')}
            >
              <RefreshCw aria-hidden="true" className="h-4 w-4" />
              Refresh
            </button>
          </div>
        </div>

        {/* KPI Strip */}
        <div className="mt-6 grid grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-4">
          <div className="rounded-lg border border-line bg-surface-2 p-4">
            <p className="text-xs font-medium uppercase tracking-wider text-fg-muted">Registered A2A Agents</p>
            <p className="mt-1 text-2xl font-bold text-fg">{registry?.total_agents ?? 5}</p>
            <p className="mt-1 text-xs text-fg-muted">Orchestrator + 4 Specialist Agents</p>
          </div>
          <div className="rounded-lg border border-line bg-surface-2 p-4">
            <p className="text-xs font-medium uppercase tracking-wider text-fg-muted">Official MCP Server Tools</p>
            <p className="mt-1 text-2xl font-bold text-fg">{mcp?.tools.length ?? 11}</p>
            <p className="mt-1 text-xs text-fg-muted">JSON-RPC 2.0 ({mcp?.protocol_version ?? '2024-11-05'})</p>
          </div>
          <div className="rounded-lg border border-line bg-surface-2 p-4">
            <p className="text-xs font-medium uppercase tracking-wider text-fg-muted">Learned Payee Rules</p>
            <p className="mt-1 text-2xl font-bold text-fg">{rules.length}</p>
            <p className="mt-1 text-xs text-fg-muted">4-Stage Closed-Loop Categorization</p>
          </div>
          <div className="rounded-lg border border-line bg-surface-2 p-4">
            <p className="text-xs font-medium uppercase tracking-wider text-fg-muted">Weakest OTel Step</p>
            <p className="mt-1 truncate text-base font-bold text-fg">
              {weakestStep ? weakestStep.step_name : 'All Spans Nominal'}
            </p>
            <p className="mt-1 text-xs text-fg-muted">
              {weakestStep
                ? `Avg ${String(weakestStep.avg_duration_ms)} ms · Max ${String(weakestStep.max_duration_ms)} ms`
                : 'OpenTelemetry Tracer Active'}
            </p>
          </div>
        </div>

        {/* Live 4-Agent Verification Result Banner */}
        {verification ? (
          <div
            className="mt-4 rounded-lg border border-line bg-accent-soft p-4 text-sm text-accent-text"
            role="status"
            data-testid="workflow-verification-banner"
          >
            <p className="font-semibold">
              4-Agent Pipeline Verification: {verification.workflow_status} ({String(verification.agents_invoked)} Agent Cards Invoked)
            </p>
            <p className="mt-1 text-xs">
              Ingestion Sanitized: <strong>{String(verification.sanitized_rows)} rows</strong> · Balance Math:{' '}
              <strong>{verification.reconciled_math_status}</strong> · Learned Rules Matched:{' '}
              <strong>{String(verification.learned_rules_applied)}</strong> · Proactive EMI/Cash Shortfall Alerts:{' '}
              <strong>{String(verification.proactive_alerts_count)}</strong>
            </p>
          </div>
        ) : null}

        {error ? (
          <div className="mt-4 rounded-lg border border-line bg-danger-soft p-3 text-sm text-danger" role="alert">
            {error}
          </div>
        ) : null}
      </section>

      {loading && !registry ? (
        <div className="flex items-center justify-center gap-2 py-16 text-sm text-fg-muted" role="status">
          <Spinner /> Loading Agent Registry &amp; Learned Rules…
        </div>
      ) : null}

      {/* Master Category Resolution & Ledger Intelligence Console */}
      <section
        className="rounded-xl border border-line bg-surface p-6 shadow-sm"
        data-testid="master-resolution-console"
      >
        <div className="flex flex-col gap-4 lg:flex-row lg:items-center lg:justify-between">
          <div>
            <div className="flex flex-wrap items-center gap-2">
              <span className="inline-flex items-center gap-1.5 rounded-full bg-accent-soft px-3 py-1 text-xs font-semibold text-accent-text">
                <Sparkles aria-hidden="true" className="h-3.5 w-3.5" />
                Master Agent · Multi-Agent Category Resolution
              </span>
              <span className="text-xs font-mono text-fg-muted">
                orchestrator → ingestion · reconciliation · clarification · advisor
              </span>
            </div>
            <h3 className="mt-2 text-lg font-bold text-fg">
              Master Transaction Category Resolver &amp; Ledger Intelligence
            </h3>
            <p className="mt-1 text-sm text-fg-muted">
              Coordinates all 4 specialist agents to inspect unverified (<code className="font-mono">requires_human_review</code>),
              low-confidence (<code className="font-mono">&lt;85%</code>), and <code className="font-mono">Other</code> rows using
              cross-row reimbursement pairing, VPA/merchant ledger history, internal FD sweep math, UPI remark semantics, and
              persistent <code className="font-mono">learned_rules</code>.
            </p>
          </div>
          <div className="flex flex-wrap items-center gap-2">
            <button
              type="button"
              onClick={() => {
                void handleRunMasterResolution(false);
              }}
              disabled={masterBusy || loading}
              className={buttonClass('secondary', 'sm')}
              data-testid="master-analyze-btn"
            >
              <Sparkles aria-hidden="true" className="h-4 w-4" />
              {masterBusy ? 'Analyzing Ledger…' : 'Analyze Ledger (Dry Run)'}
            </button>
            <button
              type="button"
              onClick={() => {
                void handleRunMasterResolution(true);
              }}
              disabled={masterBusy || loading}
              className={buttonClass('primary', 'sm')}
              data-testid="master-auto-resolve-btn"
            >
              <CheckCircle2 aria-hidden="true" className="h-4 w-4" />
              {masterBusy ? 'Updating Ledger…' : 'Auto-Resolve & Update All'}
            </button>
            {masterReport && masterReport.decisions.length > 0 ? (
              <button
                type="button"
                onClick={() => {
                  void handleApplyAllDecisions();
                }}
                disabled={masterBusy}
                className={buttonClass('primary', 'sm')}
                data-testid="master-commit-all-btn"
              >
                <CheckCircle2 aria-hidden="true" className="h-4 w-4" />
                Commit All ({String(masterReport.decisions.length)}) &amp; Teach Rules
              </button>
            ) : null}
          </div>
        </div>

        {masterNotice ? (
          <div
            className="mt-4 rounded-lg border border-line bg-success-soft p-3 text-xs font-medium text-success"
            role="status"
            data-testid="master-resolution-notice"
          >
            {masterNotice}
          </div>
        ) : null}

        {masterReport ? (
          <div className="mt-5 space-y-4">
            {/* Summary Breakdown Strip */}
            <div className="grid grid-cols-1 gap-3 sm:grid-cols-3">
              <div className="rounded-lg border border-line bg-surface-2 p-3">
                <p className="text-xs font-semibold uppercase tracking-wider text-fg-muted">
                  Decisions by Specialist Agent
                </p>
                <div className="mt-2 flex flex-wrap gap-1.5">
                  {Object.entries(masterReport.summary_by_agent).map(([agent, count]) => (
                    <span
                      key={agent}
                      className="inline-flex items-center gap-1 rounded-md border border-line bg-surface px-2 py-1 font-mono text-xs text-fg"
                    >
                      {agent}: <strong>{String(count)}</strong>
                    </span>
                  ))}
                </div>
              </div>
              <div className="rounded-lg border border-line bg-surface-2 p-3">
                <p className="text-xs font-semibold uppercase tracking-wider text-fg-muted">
                  Recommended Categories
                </p>
                <div className="mt-2 flex flex-wrap gap-1.5">
                  {Object.entries(masterReport.summary_by_category).map(([cat, count]) => (
                    <span
                      key={cat}
                      className="inline-flex items-center gap-1 rounded-md bg-accent-soft px-2 py-1 text-xs font-semibold text-accent-text"
                    >
                      {cat}: {String(count)}
                    </span>
                  ))}
                </div>
              </div>
              <div className="rounded-lg border border-line bg-surface-2 p-3">
                <p className="text-xs font-semibold uppercase tracking-wider text-fg-muted">
                  Evidence &amp; Logic Tiers Used
                </p>
                <div className="mt-2 flex flex-wrap gap-1.5">
                  {Object.entries(masterReport.summary_by_tier).map(([tier, count]) => (
                    <span
                      key={tier}
                      className="inline-flex items-center gap-1 rounded-md border border-line bg-surface px-2 py-1 font-mono text-xs text-fg-muted"
                    >
                      {tier}: <strong className="text-fg">{String(count)}</strong>
                    </span>
                  ))}
                </div>
              </div>
            </div>

            {masterReport.decisions.length === 0 ? (
              <div className="rounded-lg border border-line bg-surface-2 p-6 text-center text-sm text-fg-muted">
                All ledger transactions are already verified with high confidence and categorized cleanly (0 pending{' '}
                <code className="font-mono">Other</code> or unverified rows found).
              </div>
            ) : (
              <div className="overflow-x-auto rounded-lg border border-line">
                <table className="w-full border-collapse text-left text-xs">
                  <thead>
                    <tr className="border-b border-line bg-surface-2 uppercase tracking-wider text-fg-muted">
                      <th className="p-3 font-semibold">Row / Date</th>
                      <th className="p-3 font-semibold">Narration &amp; Amount</th>
                      <th className="p-3 font-semibold">Current → Target Category</th>
                      <th className="p-3 font-semibold">Agent &amp; Logic Tier</th>
                      <th className="p-3 font-semibold">Data &amp; Logic Reasoning</th>
                      <th className="p-3 text-right font-semibold">Action</th>
                    </tr>
                  </thead>
                  <tbody className="divide-y divide-line">
                    {masterReport.decisions.map((d) => {
                      const edit = editedDecisions[d.id] ?? {
                        category: d.recommended_category,
                        type: d.recommended_type,
                      };
                      const isApplied = Boolean(appliedRowIds[d.id]);
                      return (
                        <tr key={d.id} className="align-top">
                          <td className="whitespace-nowrap p-3 font-mono">
                            <div className="font-bold text-fg">#{String(d.id)}</div>
                            <div className="text-fg-muted">{d.date}</div>
                          </td>
                          <td className="max-w-xs p-3">
                            <div className="font-mono font-semibold text-fg">
                              ₹{d.amount.toLocaleString('en-IN', { minimumFractionDigits: 2 })}
                            </div>
                            <div className="mt-0.5 break-words text-fg-muted">{d.description}</div>
                            {d.payee_pattern ? (
                              <div className="mt-1 font-mono text-[11px] text-accent-text">
                                pattern: {d.payee_pattern}
                              </div>
                            ) : null}
                          </td>
                          <td className="p-3">
                            <div className="mb-1.5 text-[11px] text-fg-muted">
                              Was: <span className="font-mono">{d.current_type}:{d.current_category}</span>
                            </div>
                            <div className="flex flex-col gap-1.5 sm:flex-row">
                              <select
                                aria-label={`Transaction type for #${String(d.id)}`}
                                value={edit.type}
                                onChange={(e) => {
                                  const nextType = e.target.value;
                                  setEditedDecisions((prev) => ({
                                    ...prev,
                                    [d.id]: { ...edit, type: nextType },
                                  }));
                                }}
                                className={`${inputClass} h-8 px-2 py-1 text-xs`}
                              >
                                {TX_TYPES.map((t) => (
                                  <option key={t} value={t}>
                                    {t}
                                  </option>
                                ))}
                              </select>
                              <select
                                aria-label={`Category for #${String(d.id)}`}
                                value={edit.category}
                                onChange={(e) => {
                                  const nextCat = e.target.value;
                                  const nextType =
                                    nextCat === 'Transfer'
                                      ? 'TRANSFER'
                                      : ['Paycheck', 'Bonus', 'Interest'].includes(nextCat)
                                        ? 'INCOME'
                                        : edit.type === 'TRANSFER'
                                          ? 'EXPENSE'
                                          : edit.type;
                                  setEditedDecisions((prev) => ({
                                    ...prev,
                                    [d.id]: { category: nextCat, type: nextType },
                                  }));
                                }}
                                className={`${inputClass} h-8 px-2 py-1 text-xs`}
                              >
                                {CATEGORIES.map((cat) => (
                                  <option key={cat} value={cat}>
                                    {cat}
                                  </option>
                                ))}
                              </select>
                            </div>
                          </td>
                          <td className="whitespace-nowrap p-3">
                            <span className="inline-flex rounded-md bg-accent-soft px-2 py-0.5 font-mono text-[11px] font-semibold text-accent-text">
                              {d.deciding_agent}
                            </span>
                            <div className="mt-1 font-mono text-[11px] text-fg">{d.rule_tier}</div>
                            <div className="text-[11px] text-fg-muted">
                              Confidence: {String(d.new_confidence)}%
                            </div>
                          </td>
                          <td className="max-w-md p-3 text-fg-muted">{d.reasoning}</td>
                          <td className="whitespace-nowrap p-3 text-right">
                            {isApplied ? (
                              <span className="inline-flex items-center gap-1 rounded-full bg-success-soft px-2.5 py-1 text-xs font-semibold text-success">
                                <CheckCircle2 aria-hidden="true" className="h-3.5 w-3.5" />
                                Updated
                              </span>
                            ) : (
                              <button
                                type="button"
                                onClick={() => {
                                  void handleApplySingleDecision(d);
                                }}
                                disabled={masterBusy}
                                className={buttonClass('primary', 'sm')}
                              >
                                Apply
                              </button>
                            )}
                          </td>
                        </tr>
                      );
                    })}
                  </tbody>
                </table>
              </div>
            )}
          </div>
        ) : null}
      </section>

      {/* Section 1: Deployed A2A Agent Cards */}
      <section className="space-y-4">
        <div className="flex items-center justify-between gap-2">
          <div className="flex items-center gap-2">
            <Cpu aria-hidden="true" className="h-5 w-5 text-accent-text" />
            <h3 className="text-lg font-semibold text-fg">Deployed A2A Agent Cards (Google Cloud Agent Registry)</h3>
          </div>
          <span className="text-xs text-fg-muted">Click any skill badge below to invoke that agent directly</span>
        </div>
        <div className="grid grid-cols-1 gap-4 lg:grid-cols-2">
          {(registry?.agent_cards ?? []).map((card) => {
            const skillOutput = agentSkillOutputs[card.agent_id];
            return (
              <article
                key={card.agent_id}
                className="flex flex-col justify-between rounded-xl border border-line bg-surface p-5 shadow-sm"
                data-testid={`agent-card-${card.agent_id}`}
              >
                <div>
                  <div className="flex flex-wrap items-start justify-between gap-2">
                    <div>
                      <h4 className="text-base font-semibold text-fg">{card.name}</h4>
                      <p className="font-mono text-xs text-fg-muted">
                        id: {card.agent_id} · v{card.version} · A2A {card.protocol_version} ({card.preferred_transport})
                      </p>
                    </div>
                    <span className="inline-flex items-center rounded-full bg-success-soft px-2.5 py-0.5 text-xs font-semibold text-success">
                      {card.status}
                    </span>
                  </div>
                  <p className="mt-2 text-sm text-fg-muted">{card.description}</p>

                  <div className="mt-3 space-y-1.5 rounded-lg border border-line bg-surface-2 p-3 text-xs">
                    {card.gcp_agent_urn ? (
                      <div className="break-all">
                        <span className="font-semibold text-fg">GCP URN: </span>
                        <code className="font-mono text-fg-muted">{card.gcp_agent_urn}</code>
                      </div>
                    ) : null}
                    {card.spiffe_id ? (
                      <div className="break-all">
                        <span className="font-semibold text-fg">SPIFFE ID: </span>
                        <code className="font-mono text-fg-muted">{card.spiffe_id}</code>
                      </div>
                    ) : null}
                    <div className="break-all">
                      <span className="font-semibold text-fg">Discovery: </span>
                      <code className="font-mono text-fg-muted">{card.well_known_url}</code>
                    </div>
                  </div>
                </div>

                {card.skills.length > 0 ? (
                  <div className="mt-4 border-t border-line pt-3">
                    <p className="text-xs font-semibold uppercase tracking-wider text-fg-muted">
                      Registered Skills (Click to Invoke via A2A Task)
                    </p>
                    <div className="mt-2 flex flex-wrap gap-1.5">
                      {card.skills.map((skill) => {
                        const isRunning = skillBusyKey === `${card.agent_id}:${skill.id}`;
                        return (
                          <button
                            key={skill.id}
                            type="button"
                            title={skill.description}
                            disabled={Boolean(skillBusyKey)}
                            onClick={() => {
                              void handleInvokeAgentSkill(card.agent_id, skill.id);
                            }}
                            className="inline-flex items-center gap-1.5 rounded-md border border-line bg-surface-2 px-2.5 py-1 font-mono text-xs text-fg transition hover:border-accent-text hover:bg-accent-soft hover:text-accent-text disabled:opacity-50"
                            data-testid={`invoke-skill-${card.agent_id}-${skill.id}`}
                          >
                            <Play aria-hidden="true" className="h-3 w-3" />
                            {isRunning ? `${skill.id}…` : skill.id}
                          </button>
                        );
                      })}
                    </div>
                    {skillOutput ? (
                      <div className="mt-3 rounded-lg border border-line bg-surface-2 p-2.5 text-xs">
                        <div className="flex items-center justify-between font-mono text-[11px] font-semibold text-accent-text">
                          <span>Live A2A Output · {skillOutput.skillId}</span>
                          <button
                            type="button"
                            onClick={() => {
                              setAgentSkillOutputs((prev) =>
                                Object.fromEntries(Object.entries(prev).filter(([id]) => id !== card.agent_id)),
                              );
                            }}
                            className="text-fg-muted hover:text-fg"
                          >
                            Clear
                          </button>
                        </div>
                        <pre className="mt-1.5 max-h-40 overflow-auto whitespace-pre-wrap font-mono text-[11px] text-fg">
                          {JSON.stringify(skillOutput.output, null, 2)}
                        </pre>
                      </div>
                    ) : null}
                  </div>
                ) : null}
              </article>
            );
          })}
        </div>
      </section>

      {/* Section 2: 4-Stage Closed-Loop Categorization Learning (learned_rules) */}
      <section className="rounded-xl border border-line bg-surface p-6 shadow-sm">
        <div className="flex items-center gap-2">
          <BrainCircuit aria-hidden="true" className="h-5 w-5 text-accent-text" />
          <h3 className="text-lg font-semibold text-fg">
            Closed-Loop Categorization Memory (<code className="font-mono text-base">learned_rules</code>)
          </h3>
        </div>
        <p className="mt-1 text-sm text-fg-muted">
          Every time you clarify a flagged transaction via Voice/Text or edit a row in the Transactions table, the
          Clarification Agent extracts the normalized payee pattern, retroactively updates matching unverified rows,
          injects the rule into Gemini extraction prompts, and enforces it deterministically on future imports.
        </p>

        <form onSubmit={(e) => void handleTeachRule(e)} className="mt-4 flex flex-col gap-3 sm:flex-row sm:items-end">
          <div className="flex-1">
            <label htmlFor="learned-rule-pattern" className="block text-xs font-medium text-fg">
              Payee Pattern or Sample Bank Narration
            </label>
            <input
              id="learned-rule-pattern"
              type="text"
              value={patternInput}
              onChange={(e) => {
                setPatternInput(e.target.value);
              }}
              placeholder="e.g. UPI/RAHUL/778192837182/Monthly or BLINKIT GROCERY"
              className={`${inputClass} mt-1 h-10`}
              required
            />
          </div>
          <div className="sm:w-52">
            <label htmlFor="learned-rule-category" className="block text-xs font-medium text-fg">
              Target Category
            </label>
            <select
              id="learned-rule-category"
              value={categoryInput}
              onChange={(e) => {
                setCategoryInput(e.target.value);
              }}
              className={`${inputClass} mt-1 h-10`}
            >
              {CATEGORIES.map((cat) => (
                <option key={cat} value={cat}>
                  {cat}
                </option>
              ))}
            </select>
          </div>
          <button type="submit" disabled={ruleBusy} className={buttonClass('primary', 'md')}>
            <Plus aria-hidden="true" className="h-4 w-4" />
            {ruleBusy ? 'Saving…' : 'Teach Rule'}
          </button>
        </form>

        {ruleNotice ? (
          <p className="mt-3 rounded-lg bg-success-soft px-3 py-2 text-xs font-medium text-success" role="status">
            {ruleNotice}
          </p>
        ) : null}

        <div className="mt-5 overflow-x-auto">
          <table className="w-full border-collapse text-left text-sm">
            <thead>
              <tr className="border-b border-line text-xs uppercase tracking-wider text-fg-muted">
                <th className="py-2.5 pr-4 font-semibold">Normalized Pattern</th>
                <th className="px-4 py-2.5 font-semibold">Category</th>
                <th className="px-4 py-2.5 font-semibold">Type</th>
                <th className="px-4 py-2.5 font-semibold">Learned Via</th>
                <th className="px-4 py-2.5 font-semibold">Matches</th>
                <th className="py-2.5 pl-4 text-right font-semibold">Action</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-line">
              {rules.map((rule) => (
                <tr key={rule.payee_pattern}>
                  <td className="py-3 pr-4 font-mono text-xs font-semibold text-fg">{rule.payee_pattern}</td>
                  <td className="px-4 py-3">
                    <span className="inline-flex rounded-md bg-accent-soft px-2.5 py-0.5 text-xs font-semibold text-accent-text">
                      {rule.category}
                    </span>
                  </td>
                  <td className="px-4 py-3 text-xs text-fg-muted">{rule.transaction_type}</td>
                  <td className="px-4 py-3 font-mono text-xs text-fg-muted">{rule.source}</td>
                  <td className="px-4 py-3 text-xs font-semibold text-fg">{String(rule.hit_count)}</td>
                  <td className="py-3 pl-4 text-right">
                    <button
                      type="button"
                      onClick={() => {
                        void handleDeleteRule(rule.payee_pattern);
                      }}
                      className={buttonClass('ghost', 'sm')}
                      aria-label={`Remove rule ${rule.payee_pattern}`}
                    >
                      <Trash2 aria-hidden="true" className="h-4 w-4" />
                    </button>
                  </td>
                </tr>
              ))}
              {rules.length === 0 ? (
                <tr>
                  <td colSpan={6} className="py-6 text-center text-sm text-fg-muted">
                    No learned rules yet. Teach a rule above or clarify a transaction to populate agent memory.
                  </td>
                </tr>
              ) : null}
            </tbody>
          </table>
        </div>
      </section>

      {/* Section 3: Official MCP Server Tools & Governance Audit Trail */}
      <div className="grid grid-cols-1 gap-6 lg:grid-cols-2">
        {/* Official MCP Tool Catalogue */}
        <section className="rounded-xl border border-line bg-surface p-6 shadow-sm">
          <div className="flex items-center gap-2">
            <Wrench aria-hidden="true" className="h-5 w-5 text-accent-text" />
            <h3 className="text-lg font-semibold text-fg">Official MCP Server Tool Catalogue</h3>
          </div>
          <p className="mt-1 text-xs text-fg-muted">
            Server: <code className="font-mono text-fg">{mcp?.server_name ?? 'personal-finance-ledger-mcp'}</code> ·
            Protocol <code className="font-mono text-fg">{mcp?.protocol_version ?? '2024-11-05'}</code>
          </p>
          <div className="mt-4 max-h-96 space-y-2.5 overflow-y-auto pr-1">
            {(mcp?.tools ?? []).map((tool) => {
              const output = mcpOutputs[tool.name];
              const isRunning = mcpBusyTool === tool.name;
              return (
                <div key={tool.name} className="rounded-lg border border-line bg-surface-2 p-3">
                  <div className="flex flex-wrap items-center justify-between gap-2">
                    <div className="flex items-center gap-2">
                      <code className="font-mono text-xs font-bold text-fg">{tool.name}</code>
                      {tool.required_params.length > 0 ? (
                        <span className="text-[11px] text-fg-muted">
                          params: <code className="font-mono">{tool.required_params.join(', ')}</code>
                        </span>
                      ) : (
                        <span className="text-[11px] text-fg-muted">read-only</span>
                      )}
                    </div>
                    <button
                      type="button"
                      disabled={Boolean(mcpBusyTool)}
                      onClick={() => {
                        void handleRunMcpTool(tool.name);
                      }}
                      className={buttonClass('secondary', 'sm')}
                      data-testid={`run-mcp-tool-${tool.name}`}
                    >
                      <Play aria-hidden="true" className="h-3 w-3" />
                      {isRunning ? 'Running…' : 'Run Tool'}
                    </button>
                  </div>
                  <p className="mt-1 text-xs text-fg-muted">{tool.description}</p>
                  {output ? (
                    <pre className="mt-2 max-h-36 overflow-auto whitespace-pre-wrap rounded border border-line bg-surface p-2 font-mono text-[11px] text-fg">
                      {JSON.stringify(output, null, 2)}
                    </pre>
                  ) : null}
                </div>
              );
            })}
          </div>
        </section>

        {/* SPIFFE / A2A Governance Audit Trail */}
        <section className="rounded-xl border border-line bg-surface p-6 shadow-sm">
          <div className="flex items-center justify-between gap-2">
            <div className="flex items-center gap-2">
              <ShieldCheck aria-hidden="true" className="h-5 w-5 text-accent-text" />
              <h3 className="text-lg font-semibold text-fg">SPIFFE &amp; A2A Governance Audit Trail</h3>
            </div>
            <Activity aria-hidden="true" className="h-4 w-4 text-fg-muted" />
          </div>
          <p className="mt-1 text-xs text-fg-muted">
            Immutable log of A2A Agent Card delegations, SPIFFE workload verification, and human-approved rule updates.
          </p>
          <div className="mt-4 max-h-96 space-y-2.5 overflow-y-auto pr-1">
            {auditLogs.map((log) => (
              <div key={log.id} className="rounded-lg border border-line bg-surface-2 p-3 text-xs">
                <div className="flex flex-wrap items-center justify-between gap-2">
                  <span className="font-mono font-bold text-fg">
                    {log.agent_id} · {log.action}
                  </span>
                  <span className="rounded-full bg-success-soft px-2 py-0.5 text-xs font-semibold text-success">
                    {log.status}
                  </span>
                </div>
                {log.spiffe_id ? (
                  <p className="mt-1 truncate font-mono text-fg-muted" title={log.spiffe_id}>
                    {log.spiffe_id}
                  </p>
                ) : null}
                {log.created_at ? <p className="mt-1 text-fg-muted">{log.created_at.slice(0, 19)} UTC</p> : null}
              </div>
            ))}
          </div>
        </section>
      </div>
    </div>
  );
}

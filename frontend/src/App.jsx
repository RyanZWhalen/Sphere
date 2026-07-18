import { useEffect, useMemo, useState } from 'react';
import {
  Background,
  Controls,
  Handle,
  Position,
  ReactFlow,
  ReactFlowProvider,
} from '@xyflow/react';
import { indexRepositoryRequiresEdges } from './topology.js';
import { commandLine, planNarrative, stepState } from './fixplan.js';

const STATUS = {
  satisfied: { label: 'Satisfied', color: '#38c987' },
  'version-mismatch': { label: 'Version mismatch', color: '#f2b84b' },
  missing: { label: 'Missing', color: '#f06f70' },
  neutral: { label: 'Unknown', color: '#647185' },
};

const SEVERITY = { satisfied: 0, 'version-mismatch': 1, missing: 2, neutral: -1 };

function basename(path = '') {
  return path.split('/').filter(Boolean).pop() || path;
}

function shortPath(path = '') {
  const parts = path.split('/').filter(Boolean);
  return parts.length > 3 ? `…/${parts.slice(-3).join('/')}` : path;
}

function worstStatus(diff = []) {
  return diff.reduce(
    (worst, item) => (SEVERITY[item.status] > SEVERITY[worst] ? item.status : worst),
    'neutral',
  );
}

function displayInterpreter(node) {
  if (!node) return 'an unknown interpreter';
  return `${node.implementation || 'Python'} ${node.version || ''}`.trim();
}

function RuntimeNode({ data }) {
  const accent = data.status && data.status !== 'neutral' ? STATUS[data.status].color : null;
  const classes = ['runtime-card', `runtime-card--${data.kind}`, data.resolved ? 'is-resolved' : '', data.pending ? 'is-pending' : ''];
  return (
    <div className={classes.join(' ')} style={accent ? { '--accent': accent } : undefined}>
      <Handle type="target" position={Position.Left} className="flow-handle" />
      <div className="card-kicker">{data.kicker}</div>
      <div className="card-title">{data.title}</div>
      <div className="card-version">{data.version}</div>
      {data.detail && <div className="card-detail">{data.detail}</div>}
      <div className="card-tags">
        {data.resolved && <span className="tag tag--current">Runs now</span>}
        {data.fix && <span className="tag tag--fix">The fix</span>}
        {data.pending && <span className="tag tag--pending">Fix preview</span>}
        {data.status && data.status !== 'neutral' && <span className="tag" style={{ color: accent }}>{STATUS[data.status].label}</span>}
      </div>
      <Handle type="source" position={Position.Right} className="flow-handle" />
    </div>
  );
}

function OtherInterpretersNode({ data }) {
  return (
    <button className="other-card" type="button" onClick={(event) => { event.stopPropagation(); data.onToggle(); }}>
      <span>Other interpreters on this machine ({data.items.length})</span>
      <span className="other-card__chevron">{data.expanded ? '−' : '+'}</span>
      {data.expanded && (
        <span className="other-card__list">
          {data.items.map((item) => `${basename(item.path)} · ${item.version || 'unknown'}`).join('\n')}
        </span>
      )}
    </button>
  );
}

const NODE_TYPES = { runtime: RuntimeNode, other: OtherInterpretersNode };

function makeModel(topology, expanded, toggleOther, planTargetId) {
  const groups = topology.nodes || {};
  const interpreters = groups.interpreters || [];
  const environments = groups.environments || [];
  const repositories = groups.repositories || [];
  const contexts = groups.contexts || [];
  const rawEdges = topology.edges || [];
  const byId = new Map([...interpreters, ...environments, ...repositories, ...contexts].map((node) => [node.id, node]));
  const repository = repositories[0];
  const context = contexts[0];
  const resolution = rawEdges.find((edge) => edge.type === 'resolves-to' && edge.from === context?.id);
  const resolvedId = resolution?.to;
  const basedOnIds = new Set(rawEdges.filter((edge) => edge.type === 'based-on').map((edge) => edge.to));
  const foregroundInterpreterIds = new Set([resolvedId, ...basedOnIds].filter(Boolean));
  const foregroundInterpreters = interpreters.filter((node) => foregroundInterpreterIds.has(node.id));
  const otherInterpreters = interpreters.filter((node) => !foregroundInterpreterIds.has(node.id));
  // A runtime can also be the target of resolves-to and based-on edges. Only
  // the repository-origin requires edge for this exact node owns inspector data.
  const requiresByTarget = indexRepositoryRequiresEdges(rawEdges, repository?.id);
  const requiresEdgeForTarget = (targetId) => {
    const edge = requiresByTarget.get(targetId);
    return edge?.to === targetId && edge?.from === repository?.id ? edge : null;
  };
  const diffFor = (id) => requiresEdgeForTarget(id)?.diff || [];

  const nodes = [];
  if (context) {
    nodes.push({
      id: context.id,
      type: 'runtime',
      position: { x: 48, y: 58 },
      data: { kind: 'context', kicker: 'Folder', title: basename(context.path), version: 'Current context', detail: shortPath(context.path) },
    });
  }
  if (repository) {
    nodes.push({
      id: repository.id,
      type: 'runtime',
      position: { x: 48, y: 242 },
      data: {
        kind: 'repository',
        kicker: 'Repository',
        title: basename(repository.path),
        version: `${repository.requirements?.length || 0} declared requirement${repository.requirements?.length === 1 ? '' : 's'}`,
        detail: shortPath(repository.path),
        status: worstStatus(repository.requirements?.map(() => ({ status: 'neutral' }))),
      },
    });
  }

  let runtimeRow = 0;
  const addInterpreter = (interpreter, column = 472) => {
    const diff = diffFor(interpreter.id);
    nodes.push({
      id: interpreter.id,
      type: 'runtime',
      position: { x: column, y: 90 + runtimeRow * 178 },
      data: {
        kind: 'interpreter',
        kicker: 'Interpreter',
        title: basename(interpreter.path),
        version: displayInterpreter(interpreter),
        detail: shortPath(interpreter.path),
        status: worstStatus(diff),
        resolved: interpreter.id === resolvedId,
        pending: interpreter.id === planTargetId,
      },
    });
    runtimeRow += 1;
  };

  const resolvedInterpreter = foregroundInterpreters.find((node) => node.id === resolvedId);
  if (resolvedInterpreter) addInterpreter(resolvedInterpreter);
  environments.forEach((environment) => {
    const diff = diffFor(environment.id);
    nodes.push({
      id: environment.id,
      type: 'runtime',
      position: { x: 472, y: 90 + runtimeRow * 178 },
      data: {
        kind: 'environment',
        kicker: environment.kind === 'uv-project' ? 'uv environment' : `${environment.kind} environment`,
        title: basename(environment.path),
        version: environment.base_link_broken ? 'Broken base link' : 'Candidate runtime',
        detail: shortPath(environment.path),
        status: worstStatus(diff),
        fix: diff.length > 0 && worstStatus(diff) === 'satisfied',
        pending: environment.id === planTargetId,
      },
    });
    runtimeRow += 1;
  });
  foregroundInterpreters.filter((node) => node.id !== resolvedId).forEach((interpreter) => addInterpreter(interpreter, 796));

  if (otherInterpreters.length) {
    nodes.push({
      id: 'other-interpreters',
      type: 'other',
      position: { x: 796, y: Math.max(90, 90 + runtimeRow * 178) },
      data: { items: otherInterpreters, expanded, onToggle: toggleOther },
    });
  }

  const visibleIds = new Set(nodes.map((node) => node.id));
  const edges = rawEdges
    .filter((edge) => visibleIds.has(edge.from) && visibleIds.has(edge.to))
    .map((edge) => {
      const color = edge.type === 'requires'
        ? STATUS[edge.verdict || 'neutral'].color
        : edge.type === 'resolves-to' ? '#d5dde8' : '#586579';
      return {
        id: `${edge.type}:${edge.from}:${edge.to}`,
        source: edge.from,
        target: edge.to,
        type: 'smoothstep',
        animated: edge.type === 'resolves-to',
        label: edge.type === 'requires' ? STATUS[edge.verdict || 'neutral'].label : undefined,
        data: edge,
        style: {
          stroke: color,
          strokeWidth: edge.type === 'requires' ? 2.4 : 1.35,
          strokeDasharray: edge.type === 'based-on' ? '6 5' : undefined,
        },
        labelStyle: edge.type === 'requires' ? { fill: color, fontSize: 11, fontWeight: 700 } : undefined,
      };
    });
  return { nodes, edges, byId, resolvedId, repository, context, requiresEdgeForTarget };
}

function beforeText(step) {
  const before = step.before || {};
  if (before.status === 'missing') return 'missing';
  return `${before.installed_version || '—'} · ${before.status}`;
}

function afterText(receipt) {
  if (!receipt) return 'pending';
  if (receipt.error) return 'failed';
  const after = receipt.after || {};
  if (after.installed_version) return `${after.installed_version} · ${after.status || 'installed'}`;
  return after.status || 'installed';
}

const STEP_BADGE = { pending: '○', satisfied: '✓', done: '✓', error: '✕' };

function FixPanel({ plan, verdict, planLoading, applying, fixError, done, receipts, onPreview, onApprove, onCancel, onCreateVenv }) {
  const canOfferFix = verdict && verdict !== 'satisfied';
  if (!canOfferFix && !plan) return null;

  return (
    <section className="fixpanel">
      <div className="fixpanel__head">
        <span className="inspector__eyebrow">Fix loop</span>
        {plan && !applying && <button type="button" className="link-btn" onClick={onCancel}>Clear</button>}
      </div>

      {!plan && (
        <button type="button" className="btn btn--go" disabled={planLoading} onClick={onPreview}>
          {planLoading ? 'Building plan…' : 'Preview fix'}
        </button>
      )}

      {fixError && <p className="fixpanel__error">{fixError}</p>}

      {plan && !plan.target.writable && (
        <div className="fixpanel__blocked">
          <strong>Can’t fix this runtime.</strong>
          <span>{plan.target.block_reason}</span>
          {plan.target.type === 'interpreter' && (
            <button type="button" className="btn btn--go fixpanel__createvenv" onClick={onCreateVenv}>Create a venv for this folder</button>
          )}
        </div>
      )}

      {plan && plan.target.writable && (
        <>
          <p className="fixpanel__narrative">{planNarrative(plan)}</p>
          <ol className="steplist">
            {plan.steps.map((step) => {
              const receipt = receipts[step.index] || step.receipt;
              const state = stepState({ ...step, receipt });
              return (
                <li className={`step step--${state}`} key={step.index}>
                  <div className="step__head">
                    <span className="step__action">{step.action}</span>
                    <span className="step__pkg">{step.package}</span>
                    <span className="step__badge">{STEP_BADGE[state] || '○'}</span>
                  </div>
                  <code className="step__cmd">{commandLine(step.command)}</code>
                  <div className="step__delta">
                    <span>{beforeText(step)}</span>
                    <span className="step__arrow">→</span>
                    <span>{afterText(receipt)}</span>
                  </div>
                  {receipt?.error && <pre className="step__err">{receipt.stderr_tail || receipt.error}</pre>}
                </li>
              );
            })}
          </ol>

          {!done && (
            <div className="fixpanel__actions">
              <button type="button" className="btn btn--go" disabled={applying} onClick={onApprove}>
                {applying ? 'Running…' : 'Approve & run'}
              </button>
              {!applying && <button type="button" className="btn btn--ghost" onClick={onCancel}>Cancel</button>}
            </div>
          )}

          {done && plan.verdict_after != null && (
            <div className={`fixpanel__outcome ${plan.verdict_after === 'satisfied' ? 'is-good' : 'is-bad'}`}>
              <strong>{plan.verdict_before} → {plan.verdict_after}</strong>
              <span>{plan.verdict_after === 'satisfied' ? 'All requirements satisfied. Graph re-scanned.' : 'Not fully resolved — see the receipts above.'}</span>
            </div>
          )}
        </>
      )}
    </section>
  );
}

function DiffInspector({ selectedTargetId, model, plan, receipts, planLoading, applying, fixError, done, onPreview, onApprove, onCancel, onCreateVenv }) {
  const resolvedInterpreter = model.byId.get(model.resolvedId);
  const targetId = selectedTargetId || model.resolvedId;
  const active = model.requiresEdgeForTarget(targetId);
  // Never fall through to any edge attached to the target. installed_version
  // is read exclusively from this exact repository -> target requires edge.
  const diff = active?.from === model.repository?.id && active?.to === targetId ? active.diff || [] : [];
  const missing = diff.filter((item) => item.status === 'missing').length;
  const mismatch = diff.filter((item) => item.status === 'version-mismatch').length;
  const summary = diff.length
    ? `This folder resolves to ${displayInterpreter(resolvedInterpreter)}, ${missing} of ${diff.length} requirements missing${mismatch ? `, ${mismatch} mismatched` : ''}.`
    : `This folder resolves to ${displayInterpreter(resolvedInterpreter)}. No declared requirements were found.`;
  return (
    <aside className="inspector">
      <div className="inspector__eyebrow">Dependency inspector</div>
      <h2>{active ? basename(model.byId.get(active.to)?.path || active.to) : 'Topology legend'}</h2>
      <p className="inspector__summary">{summary}</p>
      <div className="legend">
        {['satisfied', 'version-mismatch', 'missing'].map((status) => (
          <span key={status}><i style={{ background: STATUS[status].color }} />{STATUS[status].label}</span>
        ))}
      </div>
      <div className="inspector__rule" />
      {diff.length ? (
        <div className="diff-list">
          {diff.map((item) => (
            <article className="diff-row" key={`${item.source}:${item.requirement}`}>
              <div className="diff-row__name">{item.name}</div>
              <div className="diff-row__specifier">{item.specifier || 'any version'}</div>
              <span className="status" style={{ color: STATUS[item.status].color }}>{STATUS[item.status].label}</span>
              <div className="diff-row__installed">Installed: {item.installed_version || '—'}</div>
              <div className="diff-row__source">{item.source}</div>
            </article>
          ))}
        </div>
      ) : (
        <p className="inspector__empty">Select a repository-to-runtime edge or a runtime card to inspect its package verdicts.</p>
      )}
      <FixPanel
        plan={plan}
        verdict={active?.verdict}
        planLoading={planLoading}
        applying={applying}
        fixError={fixError}
        done={done}
        receipts={receipts}
        onPreview={onPreview}
        onApprove={onApprove}
        onCancel={onCancel}
        onCreateVenv={onCreateVenv}
      />
    </aside>
  );
}

function Graph({ topology, onTopologyChange }) {
  const [expanded, setExpanded] = useState(false);
  const [selectedTargetId, setSelectedTargetId] = useState(null);
  const [plan, setPlan] = useState(null);
  const [receipts, setReceipts] = useState({});
  const [planLoading, setPlanLoading] = useState(false);
  const [applying, setApplying] = useState(false);
  const [fixError, setFixError] = useState(null);
  const [done, setDone] = useState(false);
  const [planRequest, setPlanRequest] = useState(null);

  const planTargetId = plan?.target?.id || null;
  const model = useMemo(
    () => makeModel(topology, expanded, () => setExpanded((value) => !value), planTargetId),
    [topology, expanded, planTargetId],
  );

  const clearFix = () => {
    setPlan(null);
    setReceipts({});
    setDone(false);
    setFixError(null);
    setPlanRequest(null);
  };

  // Selecting a different runtime abandons any plan staged for the previous one.
  const selectTarget = (id) => {
    setSelectedTargetId(id);
    clearFix();
  };

  const requestPlan = async (request) => {
    setPlanLoading(true);
    clearFix();
    setPlanRequest(request);
    try {
      const response = await fetch('/api/plan', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(request),
      });
      if (!response.ok) throw new Error(`Could not build a plan (${response.status})`);
      const data = await response.json();
      setPlan(data.plan);
    } catch (reason) {
      setFixError(reason.message);
    } finally {
      setPlanLoading(false);
    }
  };

  const previewPlan = () => {
    const targetId = selectedTargetId || model.resolvedId;
    if (targetId) requestPlan({ target_id: targetId });
  };

  const previewCreateVenv = () => requestPlan({ create_venv: true });

  const handleEvent = (event) => {
    if (event.event === 'plan') setPlan(event.plan);
    else if (event.event === 'receipt') setReceipts((prev) => ({ ...prev, [event.index]: event.step.receipt }));
    else if (event.event === 'blocked') setFixError(event.reason || 'This runtime cannot be modified.');
    else if (event.event === 'stale') {
      setPlan(event.plan);
      setFixError('The environment changed since preview — showing the refreshed plan. Review and run again.');
    } else if (event.event === 'done') {
      setPlan(event.plan);
      setDone(true);
      if (event.topology) onTopologyChange(event.topology);
    }
  };

  const approveAndRun = async () => {
    if (!plan || !plan.target.writable) return;
    setApplying(true);
    setFixError(null);
    setReceipts({});
    setDone(false);
    try {
      const response = await fetch('/api/apply', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ ...(planRequest || { target_id: plan.target.id }), fingerprint: plan.fingerprint }),
      });
      if (!response.ok || !response.body) throw new Error(`Apply failed (${response.status})`);
      const reader = response.body.getReader();
      const decoder = new TextDecoder();
      let buffer = '';
      for (;;) {
        const { value, done: streamDone } = await reader.read();
        if (streamDone) break;
        buffer += decoder.decode(value, { stream: true });
        let newline;
        while ((newline = buffer.indexOf('\n')) >= 0) {
          const line = buffer.slice(0, newline).trim();
          buffer = buffer.slice(newline + 1);
          if (line) handleEvent(JSON.parse(line));
        }
      }
    } catch (reason) {
      setFixError(reason.message);
    } finally {
      setApplying(false);
    }
  };

  return (
    <div className="app-shell">
      <main className="graph-region">
        <header className="topbar">
          <div><span className="brand-dot" /> Sphere <span>Python topology</span></div>
          <div className="topbar__meta">{applying ? 'Applying fix…' : `Read-only scan · ${topology.generated_at ? 'live' : 'loading'}`}</div>
        </header>
        <ReactFlow
          nodes={model.nodes}
          edges={model.edges}
          nodeTypes={NODE_TYPES}
          fitView
          fitViewOptions={{ padding: 0.17 }}
          minZoom={0.35}
          onNodeClick={(_event, node) => selectTarget(model.requiresEdgeForTarget(node.id) ? node.id : null)}
          onEdgeClick={(_event, edge) => {
            const isRepositoryRequiresEdge = edge.data?.type === 'requires'
              && edge.data?.from === model.repository?.id
              && edge.data?.to === edge.target;
            selectTarget(isRepositoryRequiresEdge ? edge.target : null);
          }}
          proOptions={{ hideAttribution: true }}
        >
          <Background color="#1c2634" gap={26} size={1} />
          <Controls showInteractive={false} />
        </ReactFlow>
      </main>
      <DiffInspector
        selectedTargetId={selectedTargetId}
        model={model}
        plan={plan}
        receipts={receipts}
        planLoading={planLoading}
        applying={applying}
        fixError={fixError}
        done={done}
        onPreview={previewPlan}
        onApprove={approveAndRun}
        onCancel={clearFix}
        onCreateVenv={previewCreateVenv}
      />
    </div>
  );
}

export function App() {
  const [topology, setTopology] = useState(null);
  const [error, setError] = useState(null);
  useEffect(() => {
    fetch('/api/topology')
      .then((response) => {
        if (!response.ok) throw new Error(`Topology request failed (${response.status})`);
        return response.json();
      })
      .then(setTopology)
      .catch((reason) => setError(reason.message));
  }, []);
  if (error) return <div className="loading-state"><strong>Sphere could not load the topology.</strong><span>{error}</span></div>;
  if (!topology) return <div className="loading-state"><strong>Scanning Python topology…</strong><span>Querying each interpreter in isolation.</span></div>;
  return <ReactFlowProvider><Graph topology={topology} onTopologyChange={setTopology} /></ReactFlowProvider>;
}

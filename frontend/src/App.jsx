import { useEffect, useMemo, useState } from 'react';
import {
  Background,
  Controls,
  Handle,
  Position,
  ReactFlow,
  ReactFlowProvider,
} from '@xyflow/react';

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
  const classes = ['runtime-card', `runtime-card--${data.kind}`, data.resolved ? 'is-resolved' : ''];
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

function makeModel(topology, expanded, toggleOther) {
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
  const requiresByTarget = new Map();
  rawEdges.filter((edge) => edge.type === 'requires').forEach((edge) => requiresByTarget.set(edge.to, edge));
  const diffFor = (id) => requiresByTarget.get(id)?.diff || [];

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
  return { nodes, edges, byId, requiresByTarget, resolvedId, repository, context };
}

function DiffInspector({ selection, model }) {
  const resolvedInterpreter = model.byId.get(model.resolvedId);
  const fallback = model.requiresByTarget.get(model.resolvedId);
  const active = selection || fallback;
  const diff = active?.diff || [];
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
    </aside>
  );
}

function Graph({ topology }) {
  const [expanded, setExpanded] = useState(false);
  const [selection, setSelection] = useState(null);
  const model = useMemo(() => makeModel(topology, expanded, () => setExpanded((value) => !value)), [topology, expanded]);
  return (
    <div className="app-shell">
      <main className="graph-region">
        <header className="topbar">
          <div><span className="brand-dot" /> Sphere <span>Python topology</span></div>
          <div className="topbar__meta">Read-only scan · {topology.generated_at ? 'live' : 'loading'}</div>
        </header>
        <ReactFlow
          nodes={model.nodes}
          edges={model.edges}
          nodeTypes={NODE_TYPES}
          fitView
          fitViewOptions={{ padding: 0.17 }}
          minZoom={0.35}
          onNodeClick={(_event, node) => setSelection(model.requiresByTarget.get(node.id) || null)}
          onEdgeClick={(_event, edge) => setSelection(edge.data?.type === 'requires' ? edge.data : null)}
          proOptions={{ hideAttribution: true }}
        >
          <Background color="#1c2634" gap={26} size={1} />
          <Controls showInteractive={false} />
        </ReactFlow>
      </main>
      <DiffInspector selection={selection} model={model} />
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
  return <ReactFlowProvider><Graph topology={topology} /></ReactFlowProvider>;
}

export function indexRepositoryRequiresEdges(edges, repositoryId) {
  const byTarget = new Map();
  edges
    .filter((edge) => edge.type === 'requires' && edge.from === repositoryId)
    .forEach((edge) => {
      // The first exact repository -> target edge is authoritative. Any later
      // duplicate and every other edge type/source are deliberately ignored.
      if (!byTarget.has(edge.to)) byTarget.set(edge.to, edge);
    });
  return byTarget;
}

// Pure view helpers for the fix-loop UI. Kept free of React so they can be unit
// tested directly against a fixture command-plan IR (see test/fixplan.test.js).

// Render an argv array as a copy-pasteable shell command, quoting parts that carry
// whitespace or version-specifier characters so `six==1.16.0` stays one token.
export function commandLine(command = []) {
  return command
    .map((part) => (/[\s<>=!"']/.test(part) ? JSON.stringify(part) : part))
    .join(' ');
}

// Where a single step stands, from its receipt (or lack of one).
export function stepState(step) {
  const receipt = step && step.receipt;
  if (!receipt) return 'pending';
  if (receipt.error) return 'error';
  if (receipt.after && receipt.after.status === 'satisfied') return 'satisfied';
  return 'done';
}

// One-line plain-English summary of what a plan will do.
export function planNarrative(plan) {
  if (!plan) return '';
  const target = plan.target || {};
  if (!target.writable) return target.block_reason || 'This runtime cannot be modified.';
  const steps = plan.steps || [];
  if (steps.length === 0) return 'Nothing to do — this runtime already satisfies the requirements.';
  const verb = { install: 'install', upgrade: 'upgrade', downgrade: 'downgrade', uninstall: 'remove' };
  const parts = steps.map((step) => `${verb[step.action] || step.action} ${step.package}`);
  return `${steps.length} action${steps.length === 1 ? '' : 's'}: ${parts.join(', ')}.`;
}

// The before→after verdict once a plan has been applied, or null while pending.
export function planOutcome(plan) {
  if (!plan || plan.verdict_after == null) return null;
  return {
    before: plan.verdict_before,
    after: plan.verdict_after,
    fixed: plan.verdict_after === 'satisfied',
  };
}

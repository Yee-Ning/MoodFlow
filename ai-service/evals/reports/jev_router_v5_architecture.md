# Jev Router V5 architecture

## Decision and execution boundaries

```text
User query
  → RuleGuard (input, negation, hard capability constraints)
  → JevDecisionLayer
      one task clause: frozen Contract-aligned Choice V2, confidence ≥ 0.8
      multiple clauses: frozen four Noul V3 heads + per-fragment Choice
  → IntentPlan (original spans, selected route set, per-fragment routes, context)
  → TaskOrchestrator (one task per affirmative fragment, order/dependency/clarify)
  → validated MainGraph RouteDecision
  → Subgraphs only on the selected primary path

Jev/schema/plan failure → independent Legacy Hybrid Router → validated RouteDecision
```

The Noul result is a **set of route classes**. It never determines task count. A fragment Choice assigns each executable clause to one of those classes. Extra Noul classes without a grounded fragment are ignored; a fragment route missing from the Noul set is a conflict and enters Legacy fallback. The orchestrator preserves two or three tasks even when all have `tool_calling` or `general_chat` as their route.

`RuleGuard` does not select routes. It checks blank or overlong input, removes negated clauses via the syntax parser, and clarifies known capability hard limits before Jev. The orchestrator checks exact source spans, SongContext prerequisites, unresolved entity references, ordering, dependency IDs and final `RouteDecision` schema. Simple phrases use deterministic clause syntax; a compact phrase with uncertain multiple predicates enters fallback rather than being silently collapsed.

The classifier and existing LLM router remain available only inside Legacy Hybrid fallback on the V5 candidate path. Normal Jev success has zero classifier calls. The existing production MainGraph still dispatches the Legacy decision. V5 Shadow computes a candidate in a bounded background task and never sends its tasks to a Subgraph; it reuses the already running baseline decision on fallback. The V5 flag is `JEV_ROUTER_V5_SHADOW_ENABLED`; when absent, the prior `JEV_ROUTER_V4_SHADOW_ENABLED` value is accepted, and the V5 default is enabled. `JEV_ENABLED` and `JEV_SHADOW_MODE` still gate it. Setting the V5 flag to false returns MainGraph to the prior path. The old V4 branch remains in code for compatibility.

## Frozen semantics

V5 imports existing `router_criteria()` from the Capability Contract chain and the frozen Noul instructions and threshold verifier. It has no new route criteria. `frozen_thresholds()` checks mode, exact thresholds and Contract SHA256 at candidate construction; drift raises and the primary request remains on Legacy in Shadow. Frozen thresholds: CurrentSong 0.30, ToolCalling 0.25, Worldline 0.45, GeneralChat 0.50. Single Choice confidence is fixed at 0.8. The Jev alias is `jev-latest`, with the existing SDK and five-second timeout configuration. No V3 or V4 Gold changed.

## Scope and current limits

MainGraph continues to execute tasks serially even when `depends_on` is empty. V5 records independent tasks without introducing parallel dispatch. The per-fragment Choice call is orchestration support on Multi; it adds one remote request beside the four Noul questions. Hard capability guards cover known external actions and counterfactual historical simulation classes, but natural language coverage is finite. There is no live entity-resolution call in RuleGuard. Legacy fallback can still return a semantically wrong decision and is therefore not a safety proof. Candidate primary dispatch is only exercised in controlled tests, never enabled for production.

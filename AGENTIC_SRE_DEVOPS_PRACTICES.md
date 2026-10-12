# Agentic SRE and DevOps: Interview Notes

*Prepared 12 October 2026 for interview discussion. This is a practical
overview, not a claim that agentic operations have a single settled industry
standard.*

## The short version

Established SRE and DevOps practices remain the foundation: use service
objectives and actionable signals, respond to incidents with clear roles,
troubleshoot from observations, make changes safely, and learn from incidents
without blame. An agent can help gather and organize information, but it does
not replace these operating practices or the person accountable for a
production service.

Agentic SRE is an emerging application of those practices. Publicly available
operational guidance is more established for SRE/DevOps fundamentals than
for autonomous agent operations, so distinguish that guidance from prudent
design patterns for LLM-powered systems. Avoid implying that every company
uses autonomous incident response or that MCP, RAG, or an agent framework is
itself an SRE practice.

## Practices that transfer directly from SRE and DevOps

| Practice | What it means operationally | How an agent can help |
| --- | --- | --- |
| **SLOs and useful alerts** | Define reliability in terms of user-visible service behavior. Alerts should be actionable, not merely unusual-looking data. | Summarize the SLO impact and relevant signals; do not let a fluent explanation substitute for the measurements. |
| **Structured incident response** | Declare and coordinate incidents deliberately, make roles clear, keep a timeline, and communicate status. During an incident, mitigation and coordination matter alongside diagnosis. | Draft a timeline, summarize updates, and retrieve context for responders. It should not silently take over incident command. |
| **Evidence-led troubleshooting** | Compare observations with a system model, form hypotheses, then look for confirming and disconfirming evidence. Restore service safely; root cause can remain uncertain during mitigation. | Search telemetry, code, and runbooks; distinguish observed facts from hypotheses; cite where each observation came from. |
| **Safe delivery and operations** | Make changes small and controlled, automate repeatable checks, monitor outcomes, and have a way to recover. DevOps includes shared ownership and feedback, not just tool adoption. | Prepare a change proposal or checklist. Any production change needs the same authorization, testing, monitoring, and rollback discipline as a human-initiated change. |
| **Learning after incidents** | Record impact, contributing factors, actions, and follow-up. Blameless postmortems focus on improving systems and conditions rather than assigning personal fault. | Assemble a draft from the incident record, with sources and uncertainties visible for human review. |
| **Observability** | Use metrics, logs, traces, and events to understand system behavior; retain enough context to investigate and communicate. | Correlate signals and summarize tool activity. Log model/tool choices and outcomes in a way that supports audit and debugging, while protecting sensitive data. |

These are established operational principles. The agent-specific mechanisms in
the next sections are implementation patterns to apply those principles; they
should not be presented as universal industry standards.

## Prudent patterns for an agent in an operations workflow

### 1. Start with assistance, not unrestricted autonomy

A useful progression is:

1. **Offline evaluation:** test against known incidents and synthetic cases.
2. **Shadow or advisory use:** produce summaries and suggestions without
   changing systems.
3. **Bounded read-only investigation:** let the agent call allow-listed search
   tools with scoped inputs, limits, and audit records.
4. **Human-approved actions:** prepare a specific, reviewable action and its
   expected impact; require explicit approval before execution.
5. **Narrow automation, only if justified:** consider unattended actions only
   for well-understood, low-risk, reversible operations with strong
   preconditions, post-checks, and rollback.

This is a risk-based rollout suggestion, not a formal maturity model. The
important idea is to establish evidence that a capability is reliable before
expanding its permissions or blast radius. High-impact, destructive, or
ambiguous production operations should remain under explicit human control.
That bias toward the least complex effective design is also reflected in
Microsoft's current agent-orchestration guidance; it is useful vendor
architecture advice, not an industry-wide maturity standard.

### 2. Put hard controls around tools

Treat the model as an untrusted planner, not as the enforcement boundary.
Enforce policy in code and infrastructure:

- expose narrow, purpose-built tools rather than arbitrary shell, SQL, or
  cloud-admin access;
- validate tool names, arguments, targets, and resource scope;
- default to read-only access and least privilege;
- bound execution time, result size, rate, and number of calls;
- make retries and repeated requests safe where possible;
- require a separate authorization path for state-changing actions;
- record who or what requested an action, what was approved, and what
  actually ran; redact secrets and sensitive data.

Retrieved documents, logs, code, and tool results are data, not trusted
instructions. A runbook or log line should not be able to override system
policy or grant the agent new permissions. MCP standardizes how clients and
servers exchange capabilities; it does not, by itself, make a tool safe or
authorize its use.

### 3. Make evidence and uncertainty reviewable

Keep a distinction between:

- **Observed:** present in an incident payload or returned by a named source.
- **Inferred:** a possible explanation, with confidence and supporting
  observations.
- **Unknown:** information not yet established.
- **Proposed:** a query or action that has not necessarily been executed.

Keep source identifiers, timestamps, query parameters, and bounded result
previews with the investigation. Validate citations against sources actually
collected rather than trusting source names invented in generated text. This
checks that a cited source exists; it does not prove that a generated
paraphrase is accurate, so the reviewer should compare claims with the
underlying result. A similar past incident can suggest a line of
investigation; it does not prove the current cause.

For retrieval-augmented generation, prefer owned, current, searchable
runbooks and incident records. Track document identity and freshness, return
relevant excerpts with source references, and evaluate whether retrieval
surfaces the right material. RAG describes retrieval plus model grounding;
MCP is a protocol through which a client may discover and call a retrieval
tool. They are complementary, not equivalent.

### 4. Evaluate the whole workflow

Do not judge an agent only by whether its final answer sounds plausible.
Build a representative, versioned test set and measure at least:

- whether it selects the right tool and supplies valid, appropriately scoped
  arguments;
- whether retrieved results are relevant and citations point to real results;
- whether conclusions are supported by those results, and uncertainty is
  preserved;
- whether it escalates or abstains when evidence is missing or conflicting;
- whether it ever attempts an unauthorized or unsafe action;
- latency, failures, tool-call count, token/cost use, and the usefulness of
  the resulting investigation to an operator.

Include negative cases: empty search results, stale or conflicting runbooks,
malformed tool output, unavailable dependencies, misleading retrieved text,
and requests that exceed the agent's scope. Re-run these tests when prompts,
models, tools, data, or policies change. Preserve a human fallback.

### 5. Observe the agent as another production component

Give each investigation a correlation ID. Capture model and prompt/version
metadata, tool name and validated arguments, timing, outcome, errors, approval
decisions, and final report. Use traces and metrics to find slow or failing
steps; redact credentials and avoid retaining raw sensitive content without a
clear need and policy. Measure operational benefit too: time to gather
context, responder effort, incident impact, and the quality of follow-up.

Agent telemetry should complement service telemetry, not replace the service's
SLIs, logs, traces, or incident record. OpenTelemetry's GenAI conventions are
an evolving effort; check their status and data-sensitivity implications
before adopting them as a stable contract.

## Technology terms worth keeping distinct

| Term | What it is | What it does not guarantee |
| --- | --- | --- |
| **RAG** | A pattern that retrieves relevant material and supplies it to a model as context. | Correct retrieval, current documents, truthful synthesis, or authorization. |
| **MCP** | A protocol for clients and servers to expose and use tools and other capabilities. | The agent's choice of when to call a tool, safe permissions, or a retrieval/ranking strategy. |
| **Agent framework (for example, LangGraph)** | An implementation option for representing stateful workflows, transitions, retries, and possibly pause/resume. | Better incident decisions by itself. Use it when workflow requirements justify the added abstraction. |

## A practical incident-assistant flow

1. Receive and validate an alert; attach an incident identifier and relevant
   service/SLO context.
2. Retrieve only the relevant, permitted telemetry, code, and operational
   knowledge.
3. Let the agent summarize what was observed, propose and test hypotheses
   through bounded tools, and preserve the evidence trail.
4. Have a responder decide whether to mitigate, continue investigating, or
   escalate. Require approval for actions that change production.
5. Produce a report with impact, evidence, confidence, unknowns, actions
   actually taken, and follow-up owners.
6. Review the outcome and feed appropriate lessons back into runbooks, tests,
   alerts, or system design.

The workflow supports responders; it does not automate away incident
coordination, service ownership, or engineering judgment.

## An honest interview framing for this project

> “I’m learning how agentic workflows could support SRE work, and built a
> local proof of concept around incident investigation. It uses synthetic
> Splunk-style fixtures, local code and keyword-based knowledge search, a
> bounded tool loop, validated structured output, and a report that cites
> sources collected in that run. It is not connected to production, does not
> use a real MCP transport, and cannot execute remediation. The design lesson
> I’m exploring is how to make investigation more useful while keeping
> evidence, permissions, and human approval explicit.”

That describes what the prototype demonstrates without presenting it as
production SRE experience. In a discussion, it is reasonable to say that the
agent/tooling ecosystem is moving quickly, while incident command, least
privilege, safe change management, observable systems, and learning from
failures remain the dependable engineering foundation.

## References

1. Google, [Incident Response](https://sre.google/workbook/incident-response/)
   — incident coordination, roles, communication, and control.
2. Google, [Implementing SLOs](https://sre.google/workbook/implementing-slos/)
   — user-oriented reliability targets and data-informed reliability decisions.
3. Google, [Effective Troubleshooting](https://sre.google/sre-book/effective-troubleshooting/)
   — observations, hypotheses, testing, and operational judgment.
4. Google, [Monitoring Distributed Systems](https://sre.google/sre-book/monitoring-distributed-systems/)
   — monitoring, alert quality, and actionable signals.
5. Google, [Postmortem Culture: Learning from Failure](https://sre.google/sre-book/postmortem-culture/)
   — blameless learning and preventive follow-up.
6. AWS, [What is DevOps?](https://aws.amazon.com/devops/what-is-devops/)
   — shared ownership, small changes, delivery automation, monitoring, and
   feedback.
7. Model Context Protocol, [Security Best Practices](https://modelcontextprotocol.io/docs/tutorials/security/security_best_practices)
   — protocol-specific security risks and mitigations; implementation
   guidance, not a substitute for an application's own authorization policy.
8. Microsoft, [Incident Management in the Azure Well-Architected Framework](https://learn.microsoft.com/en-us/azure/well-architected/operational-excellence/incident-response)
   — cross-vendor incident-management guidance, including containment,
   triage, and post-incident learning.
9. Microsoft, [AI Agent Orchestration Patterns](https://learn.microsoft.com/en-us/azure/architecture/ai-ml/guide/ai-agent-design-patterns)
   — current vendor guidance on workflow complexity, iteration limits, and
   security boundaries; not a formal cross-industry SRE standard.
10. CNCF TAG Observability, [Cloud Native Observability Whitepaper](https://github.com/cncf/tag-observability/blob/main/whitepaper.md)
    — community guidance on designing observability into systems.
11. OpenTelemetry, [Semantic Conventions for Generative AI](https://github.com/open-telemetry/semantic-conventions-genai)
   — evolving telemetry conventions for GenAI and MCP; verify maturity and
   stability before standardizing on individual conventions.
12. OWASP, [GenAI Security Project](https://genai.owasp.org/)
   — community security guidance covering generative AI and agentic systems.

The Google and AWS references describe established SRE/DevOps practices. MCP,
GenAI telemetry, and OWASP's agentic guidance address newer technologies; they
should be discussed as developing guidance rather than proof of universal
production adoption.

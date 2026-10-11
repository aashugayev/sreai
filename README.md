# sreai

## Incident Investigation Agent POC

This repository is being used to develop a proof of concept for an AI-assisted
production-incident investigation workflow. The idea was inspired by the
operational problems described in
[`BOFA_ERF_APS_Writeup.md`](../python_tutorial/walkthrough_documents/BOFA_ERF_APS_Writeup.md):

1. Receive an incident or alert.
2. Search Splunk for the immediate error and surrounding events.
3. Run wider SPL searches for patterns and prior occurrences.
4. Search the affected codebase for relevant code and recent changes.
5. Use source-control history to identify the changes and people best placed
   to help investigate. This is evidence for routing an investigation, not an
   automatic assignment of blame.
6. Search a knowledge base for runbooks and similar incidents.
7. Produce an evidence-based report and remediation plan for a person to
   review.

This will be built in small, understandable stages. The first goal is not a
fully autonomous multi-agent system. It is a small Python program that can
send a prompt to a locally running model and display the response.

### What an Agent Is

For this project, an agent is an application loop around a language model:

1. Give the model a goal and the information collected so far.
2. Let the model request an allowed tool, such as a Splunk search.
3. Run that tool in Python.
4. Return the tool result to the model.
5. Repeat until the model can produce a final answer or a configured limit is
   reached.

The model does not connect to Splunk, Git, or a knowledge base by itself.
Python code controls which tools are available, validates their inputs, runs
them, and records their results.

### Initial Technology Choices

The POC will start with a deliberately small stack:

| Component | Initial choice | Reason |
| --- | --- | --- |
| Language | Python | Good SDK support and easy integration with HTTP APIs and MCP. |
| Model host | Ollama | Runs models locally without a per-request model API charge. |
| HTTP client | `urllib` (standard library) | Keeps the POC dependency-free. `httpx` can replace it later if async or connection pooling is needed. |
| Data validation | `dataclasses` plus explicit validators | Keeps the POC dependency-free while making accepted input and output shapes visible. |
| Tool protocol | MCP-shaped locally; MCP transport later | Local tools use MCP-compatible definitions and result shapes. Protocol transport and remote discovery wait until they simplify a proven workflow. |
| Agent framework | None initially | A small explicit loop is easier to learn and debug than a large framework. |

We can consider LangGraph when the workflow needs durable state, branching,
retries, or approval checkpoints. Starting with it now would hide the basic
mechanics we are trying to learn.

### Model and Provider Options

Agent architecture is mostly independent of the chosen model, but API details,
tool-calling quality, speed, privacy, and cost differ.

| Approach | Model cost | Advantages | Tradeoffs |
| --- | ---: | --- | --- |
| Ollama with a local model | No hosted API charge | Local data, offline use, no API key, good for learning | Uses local CPU/GPU and memory; smaller models may reason or call tools less reliably |
| OpenAI API with a low-cost model | Usage-based | Easy setup, capable models, strong structured/tool calling | Requires an API key and separate API billing |
| LiteLLM in front of local or hosted models | Depends on provider | One interface for switching providers | Adds another dependency and abstraction |
| LangGraph with a chosen model provider | Depends on provider | Stateful, controllable multi-step workflows | More concepts and code than the first POC needs |

Installing the OpenAI Python package is free, but OpenAI API requests are
normally billed by token usage. A ChatGPT subscription does not normally
include API usage. If a hosted provider is added later, configure its spending
limit and never commit its API key.

The model integration should eventually sit behind one small interface, such
as `generate_response()`. That will let the project start with Ollama and
switch to, or compare against, a hosted model without rewriting the Splunk,
source-control, or knowledge-base integrations.

### Ollama-First Setup

1. Install Ollama from [ollama.com](https://ollama.com/).
2. Open PowerShell and verify the installation:

   ```powershell
   ollama --version
   ```

3. Download and run a small model. For example:

   ```powershell
   ollama run qwen2.5:7b
   ```

4. Enter a simple prompt to confirm that the model responds, then use `/bye`
   to leave the interactive session.

Ollama normally exposes a local HTTP API at `http://localhost:11434`. Running
locally avoids model API charges, but electricity and local hardware usage
still apply.

The exact model can be changed based on the machine's available memory. The
model name should be configuration rather than hard-coded throughout the
application.

### Stage 2: The Python Client

[`ollama_client.py`](./ollama_client.py) implements stage 2: it sends one
prompt to the local Ollama server and prints the reply. It uses only the
Python standard library, so there is nothing to install beyond Ollama itself.

Settings live in [`IncidentAgent.ini`](./IncidentAgent.ini) rather than in the
code:

```ini
[Ollama]
Host:http://localhost:11434
Model:qwen2.5:7b
TimeoutSeconds:120
```

Usage:

```powershell
# Prompt as an argument
python ollama_client.py "Summarise this stack trace in one sentence."

# Prompt from standard input
Get-Content .\error.txt | python ollama_client.py

# Override the model, and show the request the client makes
python ollama_client.py -d INFO -m llama3.1:8b "Why did this job fail?"
```

All model access goes through a single `generate_response()` function. Keeping
one integration point means a hosted API or a router such as LiteLLM can be
substituted later without changing the Splunk, source-control, or
knowledge-base code added in later stages.

The client reports the common failure modes in plain language: a missing ini
file, an unknown model name, a server that is not running, and a timeout
caused by a slow cold model load.

### How Splunk Sends an Incident Signal

The normal push mechanism is a **webhook alert action**:

1. Splunk runs a scheduled or real-time saved search.
2. The search meets its alert trigger condition.
3. Splunk sends an HTTP `POST` with a JSON body to the configured webhook URL.
4. Our receiver validates the outer Splunk envelope and the fields emitted by
   the saved search.
5. The receiver converts that payload into a provider-neutral incident signal
   and returns `202 Accepted`.

The outer envelope normally includes the search SID, saved-search name, app,
owner, results link, and a `result` object. The fields inside `result` are
determined by the SPL search. For this POC, the search contract must emit
`_time`, `host`, `service`, `environment`, `severity`, and `message`.

[`sample_data/splunk_alert.json`](./sample_data/splunk_alert.json) is a
realistic example. [`splunk_receiver.py`](./splunk_receiver.py) accepts that
payload at `POST /webhooks/splunk` and prints its normalized form.

Run the receiver, and leave it running:

```powershell
python splunk_receiver.py
```

Nothing sends an alert on its own. Until the real Splunk integration exists,
you take Splunk's place from a second terminal.
[`send_test_alert.py`](./send_test_alert.py) does that:

```powershell
python send_test_alert.py
```

The response acknowledges receipt:

```json
{
  "status": "accepted",
  "incident_id": "scheduler__svc_sre__payments__..."
}
```

The sender only sees this acknowledgement. The normalized incident signal is
printed by the receiver, in the terminal where it is running.

For a single command that starts the receiver, posts the mock alert, and then
calls `ollama_client.py` with the alert as the prompt, use
[`orchestrate_local_incident.py`](./orchestrate_local_incident.py):

```powershell
python orchestrate_local_incident.py
```

The default prompt is intentionally minimal: it asks the model to repeat the
signal text without making any investigation claim. This keeps the handoff
separate from the actual reasoning step. You can override it:

```powershell
python orchestrate_local_incident.py --prompt "Echo the JSON payload and nothing else."
```

Any HTTP client works equally well:

```powershell
Invoke-RestMethod `
  -Uri http://127.0.0.1:8080/webhooks/splunk `
  -Method Post `
  -ContentType application/json `
  -InFile .\sample_data\splunk_alert.json
```

If you prefer curl, call `curl.exe` explicitly. In Windows PowerShell, `curl`
is an alias for `Invoke-WebRequest`, which takes entirely different arguments:

```powershell
curl.exe -s -X POST http://127.0.0.1:8080/webhooks/splunk `
  -H "Content-Type: application/json" `
  --data-binary "@sample_data/splunk_alert.json"
```

#### Testing the rejection paths

A webhook receiver is only as good as the payloads it refuses, so the sender
can also corrupt a payload on purpose. The corruption is applied to an
in-memory copy, leaving `sample_data/splunk_alert.json` untouched:

```powershell
python send_test_alert.py --break missing-service
```

| `--break` value | Simulates | Expected reply |
| --- | --- | --- |
| `missing-service` | saved search stopped emitting a required field | `400` |
| `missing-result` | envelope with no result object | `400` |
| `result-not-object` | result sent as a list | `400` |
| `empty-severity` | field present but blank | `400` |
| `malformed-json` | truncated or corrupted body | `400` |
| `wrong-content-type` | JSON sent as `text/plain` | `415` |
| `empty-body` | zero-length body | `400` |

The sender exits non-zero if the receiver accepts a payload it should have
rejected, or rejects a valid one, so these checks can run in a script.

#### One-command local handoff

The receiver and Ollama client are separate programs; neither one discovers
the other automatically. [`orchestrate_local_incident.py`](./orchestrate_local_incident.py)
provides the workflow glue:

```text
mock Splunk POST
  -> splunk_receiver.py validates and normalizes it
  -> in-memory incident queue
  -> orchestrator starts ollama_client.py --display-only
  -> normalized incident JSON is piped to the client's standard input
```

Run the complete transport-only demonstration:

```powershell
python orchestrate_local_incident.py
```

The script starts and stops the receiver itself, so do not start
`splunk_receiver.py` separately on the same port. It sends the sample alert,
shows the receiver's `202 Accepted` response, then displays the normalized
incident through `ollama_client.py`.

`--display-only` is important at this stage: it proves the message reached the
client but does not load a model or call Ollama. The original prompt behavior
is unchanged and remains available independently:

```powershell
python ollama_client.py "Explain this error in one sentence."
```

To orchestrate a different sample or avoid a port already in use:

```powershell
python orchestrate_local_incident.py `
  --payload .\sample_data\splunk_alert.json `
  --port 18081
```

#### Structured incident analysis

[`incident_analysis.py`](./incident_analysis.py) defines the model-output
contract as both typed dataclasses and a JSON Schema. It also performs
independent validation after generation; schema-constrained output is not
accepted merely because the model produced it.

Run the same webhook flow and request analysis:

```powershell
python orchestrate_local_incident.py --analyze
```

In analysis mode, `ollama_client.py`:

1. Validates the normalized incident input.
2. Builds a grounded prompt containing the incident and JSON Schema.
3. Calls Ollama with that schema in the API's `format` field and temperature
   set to `0`.
4. Parses the returned JSON.
5. Rejects missing or unexpected fields, invalid types, confidence outside
   `0..1`, hypotheses without evidence, and incident identity changes.
6. Prints only the validated analysis.

The validated shape is:

```json
{
  "incident_id": "scheduler__svc_sre__payments__...",
  "service": "payments-api",
  "environment": "production",
  "severity": "critical",
  "summary": "What the evidence currently shows",
  "hypotheses": [
    {
      "description": "A possible explanation, not a verified root cause",
      "confidence": 0.8,
      "evidence": ["An observation present in the incident signal"]
    }
  ],
  "recommended_queries": ["A read-only search for missing evidence"],
  "unknowns": ["A fact the current signal does not establish"]
}
```

The prompt explicitly separates observed evidence from hypotheses. Suggested
queries are plans only: no Splunk query has been executed at this stage.

The receiver intentionally does not run Ollama before replying. A production
webhook should acknowledge quickly, persist or queue the validated signal, and
perform slower investigation work asynchronously. It must also sit behind
TLS and suitable authentication or network controls; binding to
`127.0.0.1` is only for this local exercise.

#### Local Splunk-search tool

[`local_tool_registry.py`](./local_tool_registry.py) exposes local tools using
the same core shapes as MCP: a tool has a `name`, `description`, JSON
`inputSchema`, and optional `outputSchema`; discovery returns a `tools` array;
execution returns text `content`, `structuredContent`, and an explicit
`isError` value. It is an in-process registry, not yet an MCP transport.

List the available tools:

```powershell
python local_tool_registry.py list
```

Call the read-only fake Splunk tool:

```powershell
'{"query":"HikariPool waiting","service":"payments-api","environment":"production"}' |
  python local_tool_registry.py call splunk_search -
```

[`splunk_search_tool.py`](./splunk_search_tool.py) searches the deterministic
fixture in
[`sample_data/splunk_events.json`](./sample_data/splunk_events.json). Query
terms are case-insensitive and all must match somewhere in an event. Exact
`service`, `environment`, and `host` filters, inclusive `earliest`/`latest`
timestamps, and a bounded `limit` are supported. Results are newest first.

The tool deliberately does **not** accept SPL. This keeps the exercise
read-only and prevents a model from supplying arbitrary commands. It also
makes failures visible as tool errors instead of empty, success-shaped
results. At this stage calls are made manually; the explicit agent loop in
stage 6 will give the definition to Ollama and execute requests it returns.

Splunk's other similarly named interfaces serve different directions:

- The **Splunk REST API** lets our application submit searches and retrieve
  results from Splunk. That will be useful when the agent asks follow-up
  questions, but it is a pull operation.
- **HTTP Event Collector (HEC)** sends events *into* Splunk; it is not how
  Splunk sends an alert to this application.
- A **custom alert action** can run custom code and is useful when a standard
  webhook cannot meet authentication or payload requirements.

### Build Plan: Digestible Chunks

Each stage should run and be understandable before the next one begins:

1. **Local model check** *(done)* - install Ollama, run one model, and send it
   a prompt from the terminal.
2. **Small Python client** *(done)* - send one prompt to Ollama over HTTP and
   print the response.
3. **Splunk webhook ingress** *(done)* - receive a realistic alert payload,
   validate its saved-search fields, and normalize an incident signal.
4. **Typed model output** *(done)* - require and independently validate a
   schema-constrained incident analysis rather than accepting unrestricted
   prose.
5. **One local tool** *(done)* - expose a safe, MCP-shaped fake Splunk-search
   function backed by sample events, with manual discovery and execution.
6. **Explicit agent loop** - execute a requested tool, return its result to
   the model, and enforce iteration and timeout limits.
7. **Real Splunk integration** - authenticate safely, run constrained SPL,
   handle search jobs, and redact or limit returned data.
8. **Codebase investigation** - add repository search, commit history, diff,
   and ownership evidence.
9. **Knowledge-base lookup** - retrieve runbooks and previous incident
   records.
10. **Final report** - combine claims with cited evidence, confidence,
   unanswered questions, and a human-reviewed remediation plan.
11. **MCP and orchestration evaluation** - adopt MCP and possibly LangGraph
    only where they simplify proven requirements.

Security and control will be introduced with the relevant stage: secrets must
stay out of source control, tools should be read-only by default, SPL and
repository scopes should be bounded, raw logs may need redaction, and no
remediation action should run without explicit human approval.

No custom agent skill files are needed yet. They become useful when this
project has a repeatable workflow worth encoding; creating them before the
workflow is understood would add machinery without helping us learn the core
agent loop.

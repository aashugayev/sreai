"""Render validated incident analyses as source-cited review reports."""

import json
import re


MAX_SOURCE_PREVIEW_LENGTH = 800


def render_incident_report(analysis, incident, evidence_sources):
    """Build a readable report with traceable evidence and review-only actions."""
    lines = [
        '# Incident Investigation Report',
        '',
        '- **Incident:** ' + analysis.incident_id,
        '- **Service:** ' + analysis.service,
        '- **Environment:** ' + analysis.environment,
        '- **Severity:** ' + analysis.severity,
        '',
        '## Summary',
        '',
        analysis.summary,
        '',
        '### Supporting evidence',
    ]
    lines.extend(_render_citations(analysis.summary_evidence))
    lines.extend(['', '## Assessment'])

    if analysis.hypotheses:
        for hypothesis in analysis.hypotheses:
            confidence = format(hypothesis.confidence * 100, '.0f')
            lines.extend(
                [
                    '',
                    '- **' + hypothesis.description + '** — confidence '
                    + confidence + '%',
                ],
            )
            lines.extend(_render_citations(hypothesis.evidence, indent='  '))
    else:
        lines.extend(['', 'No hypotheses were proposed.'])

    lines.extend(
        [
            '',
            '## Proposed remediation',
            '',
            '**Human review required. These are suggestions only; the agent '
            'did not approve or execute any action.**',
        ],
    )
    if analysis.recommended_actions:
        for recommendation in analysis.recommended_actions:
            lines.extend(
                [
                    '',
                    '- **' + recommendation.action + '**',
                    '  Rationale: ' + recommendation.rationale,
                ],
            )
            lines.extend(
                _render_citations(recommendation.evidence, indent='  '),
            )
    else:
        lines.extend(['', 'No remediation actions were proposed.'])

    lines.extend(['', '## Unanswered questions'])
    if analysis.unknowns:
        lines.extend('- ' + unknown for unknown in analysis.unknowns)
    else:
        lines.append('- None reported.')

    lines.extend(
        [
            '',
            '## Suggested investigative queries',
            '',
            'These queries were suggested, not executed unless listed as a '
            'successful tool call under Evidence sources.',
        ],
    )
    if analysis.recommended_queries:
        lines.extend('- ' + query for query in analysis.recommended_queries)
    else:
        lines.append('- None suggested.')

    lines.extend(
        [
            '',
            '## Evidence sources',
            '',
            'Citation source IDs are validated against collected results; '
            'the cited paraphrases and conclusions still require human '
            'verification against these previews.',
            '',
        ],
    )
    lines.extend(_render_source('INCIDENT', 'Incident signal', incident))
    for source in evidence_sources:
        source_id = source['source_id']
        label = source['tool_name']
        if source['is_error']:
            label += ' — tool error; not valid evidence'
        lines.extend(
            _render_source(
                source_id,
                label,
                {
                    'arguments': source['arguments'],
                    'result': source['result'],
                },
            ),
        )
    return '\n'.join(lines) + '\n'


def _render_citations(citations, indent=''):
    return [
        indent + '- [' + citation.source_id + '] ' + citation.observation
        for citation in citations
    ]


def _render_source(source_id, label, content):
    serialized = json.dumps(content, ensure_ascii=False, sort_keys=True)
    if len(serialized) > MAX_SOURCE_PREVIEW_LENGTH:
        serialized = serialized[:MAX_SOURCE_PREVIEW_LENGTH] + '... [truncated]'
    longest_backtick_run = max(
        (
            len(run)
            for run in re.findall(r'`+', serialized)
        ),
        default=0,
    )
    fence = '`' * max(3, longest_backtick_run + 1)
    return [
        '- **[' + source_id + '] ' + label + '**',
        '  ' + fence + 'json',
        '  ' + serialized.replace('\n', '\n  '),
        '  ' + fence,
    ]

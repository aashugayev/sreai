"""Structured incident-analysis contract and standard-library validation."""

from dataclasses import asdict, dataclass
import json
from typing import Any


INCIDENT_IDENTITY_FIELDS = (
    'incident_id',
    'service',
    'environment',
    'severity',
)

ANALYSIS_FIELDS = (
    *INCIDENT_IDENTITY_FIELDS,
    'summary',
    'summary_evidence',
    'hypotheses',
    'recommended_actions',
    'recommended_queries',
    'unknowns',
)

EVIDENCE_FIELDS = (
    'source_id',
    'observation',
)

HYPOTHESIS_FIELDS = (
    'description',
    'confidence',
    'evidence',
)

RECOMMENDATION_FIELDS = (
    'action',
    'rationale',
    'evidence',
)

ANALYSIS_JSON_SCHEMA = {
    'type': 'object',
    'additionalProperties': False,
    'properties': {
        'incident_id': {'type': 'string'},
        'service': {'type': 'string'},
        'environment': {'type': 'string'},
        'severity': {'type': 'string'},
        'summary': {'type': 'string'},
        'summary_evidence': {
            'type': 'array',
            'items': {
                'type': 'object',
                'additionalProperties': False,
                'properties': {
                    'source_id': {'type': 'string'},
                    'observation': {'type': 'string'},
                },
                'required': list(EVIDENCE_FIELDS),
            },
            'minItems': 1,
        },
        'hypotheses': {
            'type': 'array',
            'items': {
                'type': 'object',
                'additionalProperties': False,
                'properties': {
                    'description': {'type': 'string'},
                    'confidence': {
                        'type': 'number',
                        'minimum': 0,
                        'maximum': 1,
                    },
                    'evidence': {
                        'type': 'array',
                        'items': {
                            'type': 'object',
                            'additionalProperties': False,
                            'properties': {
                                'source_id': {'type': 'string'},
                                'observation': {'type': 'string'},
                            },
                            'required': list(EVIDENCE_FIELDS),
                        },
                        'minItems': 1,
                    },
                },
                'required': list(HYPOTHESIS_FIELDS),
            },
        },
        'recommended_actions': {
            'type': 'array',
            'items': {
                'type': 'object',
                'additionalProperties': False,
                'properties': {
                    'action': {'type': 'string'},
                    'rationale': {'type': 'string'},
                    'evidence': {
                        'type': 'array',
                        'items': {
                            'type': 'object',
                            'additionalProperties': False,
                            'properties': {
                                'source_id': {'type': 'string'},
                                'observation': {'type': 'string'},
                            },
                            'required': list(EVIDENCE_FIELDS),
                        },
                        'minItems': 1,
                    },
                },
                'required': list(RECOMMENDATION_FIELDS),
            },
        },
        'recommended_queries': {
            'type': 'array',
            'items': {'type': 'string'},
        },
        'unknowns': {
            'type': 'array',
            'items': {'type': 'string'},
        },
    },
    'required': list(ANALYSIS_FIELDS),
}


class AnalysisValidationError(ValueError):
    """Raised when incident input or model output violates its contract."""


@dataclass(frozen=True)
class Hypothesis:
    """One evidence-linked possible explanation."""

    description: str
    confidence: float
    evidence: tuple['EvidenceCitation', ...]


@dataclass(frozen=True)
class EvidenceCitation:
    """One observation linked to a source collected during the investigation."""

    source_id: str
    observation: str


@dataclass(frozen=True)
class Recommendation:
    """One proposed action that requires human review."""

    action: str
    rationale: str
    evidence: tuple[EvidenceCitation, ...]


@dataclass(frozen=True)
class IncidentAnalysis:
    """Validated structured analysis returned by the model."""

    incident_id: str
    service: str
    environment: str
    severity: str
    summary: str
    summary_evidence: tuple[EvidenceCitation, ...]
    hypotheses: tuple[Hypothesis, ...]
    recommended_actions: tuple[Recommendation, ...]
    recommended_queries: tuple[str, ...]
    unknowns: tuple[str, ...]


def require_object(value, location):
    """Return an object or raise a location-specific validation error."""
    if not isinstance(value, dict):
        raise AnalysisValidationError(location + ' must be a JSON object')
    return value


def require_exact_fields(value, expected_fields, location):
    """Reject both missing and unexpected object fields."""
    expected = set(expected_fields)
    actual = set(value)
    missing = sorted(expected - actual)
    unexpected = sorted(actual - expected)

    if missing:
        raise AnalysisValidationError(
            location + ' is missing fields: ' + ', '.join(missing)
        )
    if unexpected:
        raise AnalysisValidationError(
            location + ' has unexpected fields: ' + ', '.join(unexpected)
        )


def require_string(value, location):
    """Return a non-empty string."""
    if not isinstance(value, str) or not value.strip():
        raise AnalysisValidationError(location + ' must be a non-empty string')
    return value.strip()


def require_string_list(value, location, allow_empty=True):
    """Return a tuple containing only non-empty strings."""
    if not isinstance(value, list):
        raise AnalysisValidationError(location + ' must be a JSON array')
    if not allow_empty and not value:
        raise AnalysisValidationError(location + ' must not be empty')

    return tuple(
        require_string(item, location + '[' + str(index) + ']')
        for index, item in enumerate(value)
    )


def parse_incident_message(message):
    """Parse and minimally validate the normalized incident JSON."""
    try:
        incident = json.loads(message)
    except json.JSONDecodeError as error:
        raise AnalysisValidationError(
            'incident message is not valid JSON at character ' + str(error.pos)
        ) from error

    incident = require_object(incident, 'incident')
    for field_name in INCIDENT_IDENTITY_FIELDS:
        require_string(incident.get(field_name), 'incident.' + field_name)

    evidence = require_object(incident.get('evidence'), 'incident.evidence')
    if not evidence:
        raise AnalysisValidationError('incident.evidence must not be empty')

    require_string(incident.get('summary'), 'incident.summary')
    return incident


def build_analysis_prompt(incident):
    """Build a grounded prompt containing the incident and output schema."""
    return (
        'Analyze the incident signal below. Return JSON only and conform '
        'exactly to the supplied JSON Schema.\n\n'
        'Rules:\n'
        '- Copy incident_id, service, environment, and severity exactly.\n'
        '- Treat only the incident payload as observed evidence.\n'
        '- Keep possible causes in hypotheses; do not present them as facts.\n'
        '- Every summary, hypothesis, and proposed action must cite at least '
        'one observation using a source_id and observation object.\n'
        '- The incident payload source_id is INCIDENT. Successful tool results '
        'include a TOOL-N source_id; cite only IDs present in the conversation.\n'
        '- Recommended actions are suggestions for human review only; do not '
        'claim that actions were approved or performed.\n'
        '- Confidence must be a number from 0 through 1.\n'
        '- recommended_queries are read-only searches that could gather '
        'missing evidence; do not claim they were executed.\n'
        '- Put unanswered questions and missing facts in unknowns.\n'
        '- Do not include markdown or fields outside the schema.\n\n'
        'Incident signal:\n'
        + json.dumps(incident, indent=2, sort_keys=True)
        + '\n\nJSON Schema:\n'
        + json.dumps(ANALYSIS_JSON_SCHEMA, indent=2, sort_keys=True)
    )


def parse_evidence_citation(value, location, available_source_ids):
    """Validate one observation and ensure it cites a collected source."""
    value = require_object(value, location)
    require_exact_fields(value, EVIDENCE_FIELDS, location)
    source_id = require_string(value['source_id'], location + '.source_id')
    if source_id not in available_source_ids:
        raise AnalysisValidationError(
            location + '.source_id does not match a collected evidence source'
        )
    return EvidenceCitation(
        source_id=source_id,
        observation=require_string(
            value['observation'],
            location + '.observation',
        ),
    )


def parse_evidence_list(value, location, available_source_ids):
    """Parse a non-empty list of citations to collected evidence."""
    if not isinstance(value, list):
        raise AnalysisValidationError(location + ' must be a JSON array')
    if not value:
        raise AnalysisValidationError(location + ' must not be empty')
    return tuple(
        parse_evidence_citation(
            citation,
            location + '[' + str(index) + ']',
            available_source_ids,
        )
        for index, citation in enumerate(value)
    )


def parse_hypothesis(value, index, available_source_ids):
    """Validate one hypothesis object."""
    location = 'analysis.hypotheses[' + str(index) + ']'
    value = require_object(value, location)
    require_exact_fields(value, HYPOTHESIS_FIELDS, location)

    confidence = value['confidence']
    if isinstance(confidence, bool) or not isinstance(confidence, (int, float)):
        raise AnalysisValidationError(location + '.confidence must be a number')
    if confidence < 0 or confidence > 1:
        raise AnalysisValidationError(
            location + '.confidence must be between 0 and 1'
        )

    return Hypothesis(
        description=require_string(
            value['description'],
            location + '.description',
        ),
        confidence=float(confidence),
        evidence=parse_evidence_list(
            value['evidence'],
            location + '.evidence',
            available_source_ids,
        ),
    )


def parse_recommendation(value, index, available_source_ids):
    """Validate one human-reviewed action proposal."""
    location = 'analysis.recommended_actions[' + str(index) + ']'
    value = require_object(value, location)
    require_exact_fields(value, RECOMMENDATION_FIELDS, location)
    return Recommendation(
        action=require_string(value['action'], location + '.action'),
        rationale=require_string(value['rationale'], location + '.rationale'),
        evidence=parse_evidence_list(
            value['evidence'],
            location + '.evidence',
            available_source_ids,
        ),
    )


def parse_analysis_response(
    response_text,
    incident,
    available_source_ids=None,
):
    """Parse, validate, and identity-check one model response."""
    try:
        payload = json.loads(response_text)
    except json.JSONDecodeError as error:
        raise AnalysisValidationError(
            'model response is not valid JSON at character ' + str(error.pos)
        ) from error

    payload = require_object(payload, 'analysis')
    require_exact_fields(payload, ANALYSIS_FIELDS, 'analysis')
    source_ids = {'INCIDENT'}
    if available_source_ids is not None:
        source_ids.update(available_source_ids)

    identity = {}
    for field_name in INCIDENT_IDENTITY_FIELDS:
        identity[field_name] = require_string(
            payload[field_name],
            'analysis.' + field_name,
        )
        expected = incident[field_name]
        if identity[field_name] != expected:
            raise AnalysisValidationError(
                'analysis.'
                + field_name
                + ' does not match incident.'
                + field_name
            )

    hypotheses_value = payload['hypotheses']
    if not isinstance(hypotheses_value, list):
        raise AnalysisValidationError('analysis.hypotheses must be a JSON array')

    return IncidentAnalysis(
        incident_id=identity['incident_id'],
        service=identity['service'],
        environment=identity['environment'],
        severity=identity['severity'],
        summary=require_string(payload['summary'], 'analysis.summary'),
        summary_evidence=parse_evidence_list(
            payload['summary_evidence'],
            'analysis.summary_evidence',
            source_ids,
        ),
        hypotheses=tuple(
            parse_hypothesis(value, index, source_ids)
            for index, value in enumerate(hypotheses_value)
        ),
        recommended_actions=tuple(
            parse_recommendation(value, index, source_ids)
            for index, value in enumerate(
                require_list(payload['recommended_actions'], 'analysis.recommended_actions')
            )
        ),
        recommended_queries=require_string_list(
            payload['recommended_queries'],
            'analysis.recommended_queries',
        ),
        unknowns=require_string_list(
            payload['unknowns'],
            'analysis.unknowns',
        ),
    )


def require_list(value, location):
    """Return an array or raise a location-specific validation error."""
    if not isinstance(value, list):
        raise AnalysisValidationError(location + ' must be a JSON array')
    return value


def analysis_to_dict(analysis):
    """Return a JSON-serializable representation of a validated analysis."""
    return asdict(analysis)

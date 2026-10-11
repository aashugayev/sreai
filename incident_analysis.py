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
    'hypotheses',
    'recommended_queries',
    'unknowns',
)

HYPOTHESIS_FIELDS = (
    'description',
    'confidence',
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
                        'items': {'type': 'string'},
                        'minItems': 1,
                    },
                },
                'required': list(HYPOTHESIS_FIELDS),
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
    evidence: tuple[str, ...]


@dataclass(frozen=True)
class IncidentAnalysis:
    """Validated structured analysis returned by the model."""

    incident_id: str
    service: str
    environment: str
    severity: str
    summary: str
    hypotheses: tuple[Hypothesis, ...]
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
        '- Every hypothesis must cite at least one observation from evidence.\n'
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


def parse_hypothesis(value, index):
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
        evidence=require_string_list(
            value['evidence'],
            location + '.evidence',
            allow_empty=False,
        ),
    )


def parse_analysis_response(response_text, incident):
    """Parse, validate, and identity-check one model response."""
    try:
        payload = json.loads(response_text)
    except json.JSONDecodeError as error:
        raise AnalysisValidationError(
            'model response is not valid JSON at character ' + str(error.pos)
        ) from error

    payload = require_object(payload, 'analysis')
    require_exact_fields(payload, ANALYSIS_FIELDS, 'analysis')

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
        hypotheses=tuple(
            parse_hypothesis(value, index)
            for index, value in enumerate(hypotheses_value)
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


def analysis_to_dict(analysis):
    """Return a JSON-serializable representation of a validated analysis."""
    return asdict(analysis)

'use strict';

// Pure field assembly only: no I/O, retained facts, lease or semantic inference.
const version = '1.0.0';
function fail(code) { const error = new TypeError(code); error.code = code; throw error; }
function record(value) {
  // CUA may pass objects from a different VM realm; prototype identity is not a field contract.
  return value !== null && typeof value === 'object' &&
    Object.prototype.toString.call(value) === '[object Object]';
}
function validateAiRelevance(value) {
  if (!record(value) || value.schema_version !== 'AiRelevanceV1') fail('invalid_ai_relevance_schema');
  const decision = value.ai_related;
  if (decision !== true && decision !== false && decision !== null) fail('invalid_ai_relevance');
  const reasonKey = decision === null ? 'unassessed_reason' : 'reasoning';
  const expected = ['schema_version', 'ai_related', reasonKey];
  if (Object.keys(value).length !== 3 || !expected.every(key => Object.hasOwn(value, key))) {
    fail('invalid_ai_relevance_fields');
  }
  const reason = value[reasonKey];
  if (typeof reason !== 'string' || !reason.trim()) fail('invalid_ai_relevance_reason');
  if (decision === null ? !/^[a-z][a-z0-9_]{0,63}$/.test(reason) : Array.from(reason).length > 2000) {
    fail('invalid_ai_relevance_reason');
  }
  return value;
}
function aiRelevance(decision, reason) {
  const value = {schema_version: 'AiRelevanceV1', ai_related: decision};
  value[decision === null ? 'unassessed_reason' : 'reasoning'] = reason;
  return validateAiRelevance(value);
}
function validateAnalyses(analyses, stream) {
  if (!record(analyses) || !['main', 'reply'].includes(stream)) fail('invalid_analysis_fields_input');
  for (const value of Object.values(analyses)) {
    if (!record(value)) fail('invalid_analysis_fields_input');
    if (stream === 'reply') validateAiRelevance(value.ai_relevance);
    else if (Object.hasOwn(value, 'ai_relevance')) fail('ai_relevance_requires_reply');
  }
  return analyses; // Preserve the model's exact decisions/text and object identity.
}
module.exports = Object.freeze({version, aiRelevance, validateAiRelevance, validateAnalyses});

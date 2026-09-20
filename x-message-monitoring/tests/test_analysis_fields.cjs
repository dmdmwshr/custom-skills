'use strict';
const test = require('node:test');
const assert = require('node:assert/strict');
const fields = require('../scripts/analysis_fields.cjs');

test('same-CUA caller objects can belong to a separate VM realm', () => {
  const analyses = require('node:vm').runInNewContext("({'1000':{ai_relevance:{schema_version:'AiRelevanceV1',ai_related:false,reasoning:'模型判断'}}})");
  assert.equal(fields.validateAnalyses(analyses, 'reply'), analyses);
  assert.throws(() => fields.validateAnalyses(new Date(), 'reply'), /invalid_analysis_fields_input/);
});

for (const decision of [true, false, null]) {
  test(`assembles exact ${decision} fields without inferring a decision`, () => {
    const reason = decision === null ? 'context_unavailable' : '  本轮模型的实际依据。  ';
    const value = fields.aiRelevance(decision, reason);
    assert.deepEqual(value, {schema_version:'AiRelevanceV1', ai_related:decision,
      [decision === null ? 'unassessed_reason' : 'reasoning']:reason});
    const analyses = {'1000': {ai_relevance:value, full_analysis:'不改分析。'}};
    assert.equal(fields.validateAnalyses(analyses, 'reply'), analyses);
    assert.equal(fields.validateAiRelevance(value), value);
  });
}
test('the observed related alias is refused before any send, not silently rewritten', () => {
  const analyses = {'1000': {ai_relevance:{schema_version:'AiRelevanceV1', related:false, reasoning:'模型依据。'}}};
  const before = JSON.stringify(analyses);
  let sends = 0;
  const send = () => { sends++; };
  assert.throws(() => send({analyses:fields.validateAnalyses(analyses, 'reply')}), /invalid_ai_relevance/);
  assert.equal(sends, 0);
  assert.equal(JSON.stringify(analyses), before);
});
test('wrong, mixed and extra fields are not coerced', () => {
  for (const decision of [undefined, 0, 1, 'false', {}, []]) {
    assert.throws(() => fields.aiRelevance(decision, '依据'), /invalid_ai_relevance/);
  }
  assert.throws(() => fields.validateAiRelevance({...fields.aiRelevance(false, '依据'), related:false}), /fields/);
  assert.throws(() => fields.validateAiRelevance({...fields.aiRelevance(null, 'context_unavailable'), reasoning:'依据'}), /fields/);
  for (const reason of ['', ' ', null, 'a'.repeat(2001)]) assert.throws(() => fields.aiRelevance(false, reason), /reason/);
  for (const reason of ['Bad-code', 'a'.repeat(65)]) assert.throws(() => fields.aiRelevance(null, reason), /reason/);
});
test('empty plans and main analyses stay unchanged; main cannot carry reply fields', () => {
  const empty = {};
  assert.equal(fields.validateAnalyses(empty, 'reply'), empty);
  const main = {'1000': {reset_analysis:{related:false}}};
  assert.equal(fields.validateAnalyses(main, 'main'), main);
  assert.throws(() => fields.validateAnalyses({'1000':{ai_relevance:fields.aiRelevance(false,'依据')}}, 'main'), /requires_reply/);
});

/** Search aliases are derived from project comments paired with identifier words, never a financial dictionary. */
function identifierWords(value) {
  return String(value || '').match(/[A-Z]+(?=[A-Z][a-z]|\b)|[A-Z]?[a-z]+|[0-9]+/g) || [];
}

/** Convert a Cyrillic abbreviation only to compare against an existing Latin identifier segment. */
function transliterate(value) {
  const alphabet={а:'a',б:'b',в:'v',г:'g',д:'d',е:'e',ё:'e',ж:'zh',з:'z',и:'i',й:'y',к:'k',л:'l',м:'m',н:'n',о:'o',п:'p',р:'r',с:'s',т:'t',у:'u',ф:'f',х:'h',ц:'ts',ч:'ch',ш:'sh',щ:'sch',ъ:'',ы:'y',ь:'',э:'e',ю:'yu',я:'ya'};
  return [...value.toLowerCase()].map(letter=>alphabet[letter] ?? letter).join('');
}

/** Build a bounded per-snapshot alias map; comments must actually contain the Cyrillic abbreviation. */
export function projectSearchAliases(nodes) {
  const result=new Map();
  for(const node of nodes) {
    const comment=String(node.comment || node.documentation?.summary || '').slice(0,2000);
    const identifiers=new Set(identifierWords(node.label).map(word=>word.toLowerCase()));
    for(const acronym of comment.match(/(?<![\p{L}\p{N}_])[А-ЯЁ]{2,8}(?![\p{L}\p{N}_])/gu) || []) {
      const latin=transliterate(acronym);
      if(identifiers.has(latin) && result.size<64)result.set(acronym.toLowerCase(),latin);
    }
  }
  return result;
}

/** Each term retains its typed form as an alternative; aliasing never mutates the user's query. */
export function discoveryTerms(query,aliases=new Map()) {
  return String(query || '').trim().toLowerCase().split(/\s+/u).filter(Boolean).map(term=>[term,...(aliases.has(term)?[aliases.get(term)]:[])]);
}

/** Comments improve findability; source strings remain plain indexed text, not executed content. */
export function discoveryText(node) {
  return `${node.label || ''} ${node.file || ''} ${String(node.comment || '').slice(0,2000)} ${String(node.documentation?.summary || '').slice(0,2000)}`.toLowerCase();
}

/** Only captured HTTP attributes establish entry priority; naming alone cannot establish an entrypoint. */
export function hasHttpEntryEvidence(node) {
  return (node.attributeFacts?.groups || []).some(group=>group.scope === 'declaration' && group.kind === 'method_declaration' && (group.attributes || []).some(attribute=>/^Http(?:Get|Post|Put|Patch|Delete|Head|Options)(?:Attribute)?$/i.test(attribute.name || '')));
}

/** Captured member/callable facts support older Graphify maps whose node kind is only symbol. */
export function isCallableDeclaration(node) {
  return ['function', 'method'].includes(node.kind) || ['method', 'constructor', 'function'].includes(node.callableFacts?.kind)
    || ['method', 'constructor', 'function'].includes(node.memberFacts?.kind);
}

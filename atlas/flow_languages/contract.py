"""Common diagram vocabulary and bounded, optional language extensions for every provider."""
import json
from pydantic import BaseModel, Field, ValidationError

VERSION = 'atlas-method-flow-v1'
STEP_KINDS = frozenset({'call','await','operation','statement','value','return','throw','break','continue','branch','loop','unknown'})
MAX_STEPS = 180
MAX_DEPTH = 24


class FlowExtension(BaseModel):
    """Rare source facts remain data with a readable fallback, never a language-specific renderer."""
    id: str = Field(pattern=r'^[a-z][a-z0-9-]*\.[a-z][a-z0-9.-]*$', max_length=100)
    version: int = Field(ge=1, strict=True)
    label: str = Field(min_length=1, max_length=400)
    detail: str = Field(default='', max_length=1000)
    data: dict = Field(default_factory=dict)
    model_config = {'extra':'forbid'}


class FlowNormalizer:
    """One global row/depth budget preserves common shapes and surfaces incomplete provider output."""
    def __init__(self, provider):
        self.provider = provider
        self.count = 0
        self.arm_count = 0
        self.sites = set()
        self.truncated = False
        self.unsupported = set()

    def extensions(self, values):
        """Validate namespaced metadata and expose its plain-text fallback to normal cards."""
        result = []
        for value in values[:8] if isinstance(values, list) else []:
            try:
                extension = FlowExtension.model_validate(value)
                if len(json.dumps(extension.data, ensure_ascii=False).encode()) > 8000:
                    raise ValueError('extension data exceeds budget')
                result.append(extension.model_dump())
            except (ValidationError, ValueError, TypeError):
                self.unsupported.add('Invalid optional language extension omitted.')
        return result

    def steps(self, values, depth=0):
        """Normalize recursive branch/loop containers without flattening conditional or deferred work."""
        if not isinstance(values, list):
            self.unsupported.add('Provider step list is invalid.')
            return []
        result = []
        for value in values:
            if self.count >= MAX_STEPS or depth > MAX_DEPTH:
                self.truncated = True
                break
            if not isinstance(value, dict):
                self.unsupported.add('Invalid provider step omitted.')
                continue
            self.count += 1
            step = dict(value)
            if step.get('kind') not in STEP_KINDS:
                self.unsupported.add('Unknown step kind uses a common incomplete card.')
                step['kind'] = 'unknown'
                step['incomplete'] = True
            step['label'] = str(step.get('label') or step['kind'])[:400]
            if step['kind'] == 'call' and isinstance(step.get('site'), dict):
                self.sites.add(tuple(step['site'].get(key) for key in ('line','column','endLine','endColumn')))
            for key in ('steps','condition','body','update'):
                if isinstance(step.get(key), list):
                    step[key] = self.steps(step[key], depth + 1)
            if isinstance(step.get('arms'), list):
                arms = []
                for arm in step['arms']:
                    if self.arm_count >= MAX_STEPS:
                        self.truncated = True
                        break
                    if not isinstance(arm, dict):
                        self.unsupported.add('Invalid branch arm omitted.')
                        continue
                    self.arm_count += 1
                    arms.append({**arm, 'label':str(arm.get('label') or '')[:400],
                                 'steps':self.steps(arm.get('steps', []), depth + 1)})
                step['arms'] = arms
            elif step['kind'] == 'branch':
                self.unsupported.add('Branch alternatives are unavailable.')
                step.update(kind='unknown', incomplete=True)
            if 'extensions' in step:
                step['extensions'] = self.extensions(step['extensions'])
            result.append(step)
        return result


def normalize_description(result, provider, max_events):
    """Stamp one schema for C# and new providers; the UI never chooses rendering by language."""
    result['flowProvider'] = {'id':provider, 'contractVersion':VERSION}
    events = result.get('events') or []
    result['events'] = events[:max_events]
    result['eventsTruncated'] = bool(result.get('eventsTruncated')) or len(events) > max_events
    flow = result.get('methodFlow')
    if not isinstance(flow, dict):
        return result
    normalizer = FlowNormalizer(provider)
    steps = normalizer.steps(flow.get('steps', []))
    if normalizer.truncated:
        # A common limit must not leave navigable events for calls removed from the tree.
        result['events'] = [event for event in result['events'] if isinstance(event, dict) and (
            event.get('kind') not in {'call','log','span'} or
            tuple(event.get(key) for key in ('line','column','endLine','endColumn')) in normalizer.sites)]
        result['eventsTruncated'] = True
    unsupported = list(dict.fromkeys([*(flow.get('unsupported') or []), *sorted(normalizer.unsupported)]))
    result['methodFlow'] = {**flow, 'contractVersion':VERSION, 'steps':steps,
                            'truncated':bool(flow.get('truncated')) or normalizer.truncated,
                            'unsupported':unsupported}
    return result

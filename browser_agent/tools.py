"""Only registered, strictly validated actions can reach the browser."""
import json
from typing import Literal
from urllib.parse import urlsplit
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class Args(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)

class Navigate(Args):
    url: str = Field(min_length=1, max_length=2048)

    @field_validator('url')
    @classmethod
    def web_url(cls, value):
        p = urlsplit(value)
        if p.scheme not in ('http', 'https') or not p.hostname or p.username or p.password:
            raise ValueError('Only HTTP(S) URLs without credentials are allowed')
        return value

class Ref(Args):
    expected_outcome: str | None = Field(default=None, max_length=500)
    ref: str = Field(pattern=r'^s\d+e\d+$')

class TypeText(Ref):
    text: str = Field(max_length=4000)

class UploadFile(Ref):
    relative_path: str = Field(min_length=1, max_length=500)

    @field_validator('relative_path')
    @classmethod
    def safe_relative_path(cls, value):
        normalized = value.replace('\\', '/')
        parts = normalized.split('/')
        if any(ord(c)<32 for c in value) or normalized.startswith('/') or any(part in {'', '..'} for part in parts) or ':' in parts[0]:
            raise ValueError('Upload path must be a relative path inside the configured upload root')
        return value

class FindInPage(Args):
    refinement_reason: str | None = Field(default=None, min_length=12, max_length=300)
    text: str = Field(min_length=1, max_length=300)
    max_results: int = Field(default=8, ge=1, le=20)

class ExtractPage(Args):
    query: str | None = Field(default=None, min_length=1, max_length=300)
    max_sections: int = Field(default=10, ge=1, le=20)
    max_chars_per_section: int = Field(default=1200, ge=200, le=3000)

class ExtractCollection(Args):
    query: str | None = Field(default=None, min_length=1, max_length=300)
    max_items: int = Field(default=20, ge=1, le=50)
    max_chars_per_item: int = Field(default=900, ge=120, le=2000)

class FormField(Args):
    ref: str = Field(pattern=r'^s\d+e\d+$')
    kind: Literal['text', 'select']
    value: str = Field(max_length=4000)

class FillForm(Args):
    expected_outcome: str | None = Field(default=None, max_length=500)
    fields: list[FormField] = Field(min_length=1, max_length=12)

    @model_validator(mode='after')
    def unique_refs(self):
        refs = [f.ref for f in self.fields]
        if len(refs) != len(set(refs)):
            raise ValueError('Each form ref may appear only once')
        return self

class SelectOption(Ref):
    value: str = Field(min_length=1, max_length=1000)

class Press(Args):
    expected_outcome: str | None = Field(default=None, max_length=500)
    key: Literal[
        'Enter', 'Tab', 'Escape', 'ArrowDown', 'ArrowUp', 'ArrowLeft',
        'ArrowRight', 'Space', 'PageDown', 'PageUp', 'Home', 'End',
        'Backspace'
    ]

class Scroll(Args):
    direction: Literal['up', 'down']
    amount: int = Field(default=600, ge=100, le=3000)

class Wait(Args):
    seconds: float = Field(default=1.0, ge=0, le=5)

class Question(Args):
    question: str = Field(min_length=1, max_length=1000)

class Finish(Args):
    result: str = Field(min_length=1, max_length=8000)
    status: Literal['complete', 'blocked']

class Plan(Args):
    steps: list[str] = Field(min_length=1, max_length=6)

class Verify(Args):
    outcome: Literal['achieved', 'not_achieved', 'uncertain']
    evidence: str = Field(
        min_length=3,
        max_length=1000,
        description=(
            "Exact literal substring copied verbatim from the CURRENT observation "
            "or a read_page result for that same current state. Do not paraphrase, "
            "summarize, infer, or invent evidence. For form values, copy the exact "
            "serialized value visible in the observation, for example: "
            "\\\"value\\\": \\\"2\\\"."
        ),
    )
    explanation: str = Field(
        min_length=1,
        max_length=500,
        description="Briefly explain why the quoted evidence proves the expected outcome.",
    )

class Tab(Args):
    index: int = Field(ge=0, le=99)

class ManageTabs(Args):
    action: Literal['list', 'select', 'new', 'close']
    index: int | None = Field(default=None, ge=0, le=99)
    url: str | None = Field(default=None, max_length=2048)
    expected_outcome: str | None = Field(default=None, max_length=500)

    @model_validator(mode='after')
    def valid_operation(self):
        if self.action in {'select', 'close'} and self.index is None:
            raise ValueError('index is required for select/close')
        if self.action != 'new' and self.url is not None:
            raise ValueError('url is only valid for action=new')
        if self.url is not None:
            p = urlsplit(self.url)
            if p.scheme not in ('http', 'https') or not p.hostname or p.username or p.password:
                raise ValueError('Only HTTP(S) URLs without credentials are allowed')
        return self

class Note(Args):
    key: str | None = Field(default=None, min_length=1, max_length=80)
    fact: str = Field(min_length=1, max_length=1000)

class Read(Args):
    offset: int = Field(default=0, ge=0, le=200000)

REGISTRY = {
    'verify_action': (Verify, 'Verify the pending important action using an exact quotation from the CURRENT observation (text or element state). Explain how it proves the intended outcome. A changed page alone is not proof. Use not_achieved for validation/rejection, uncertain if still waiting.'),
    'manage_tabs': (ManageTabs, 'List, select, open, or close browser tabs. Closing a tab is consequential and requires confirmation. Prefer this over inventing popup/window assumptions.'),
    'switch_tab': (Tab, 'Switch to an existing tab index from the current observation.'),
    'find_in_page': (FindInPage, 'Search all currently known interactive page elements plus visible page text for a short phrase. Returns matching semantic elements and refs without changing the page; use it when the normal snapshot is crowded or the target is not obvious.'),
    'extract_page': (ExtractPage, 'Read a compact structured overview of the current page: headings, semantic sections/forms and links. Optionally focus on a short query. This is read-only and useful before acting on unfamiliar pages.'),
    'extract_collection': (ExtractCollection, 'Read repeated page items such as mail rows, product/job cards, list items or table rows into compact structured records. Optionally filter/rank by a short query. Returned control refs remain valid for the immediately following decision.'),
    'fill_form': (FillForm, 'Fill several stable text/select fields from the current observation in one action. Values are not logged. Use individual actions instead when one field dynamically changes the next field.'),
    'navigate': (Navigate, 'Open an HTTP(S) URL.'),
    'click': (Ref, 'Click a current observation element. Normal navigation/UI clicks are autonomous; risky irreversible actions may require confirmation.'),
    'type_text': (TypeText, 'Fill a textbox. Values are not logged.'),
    'upload_file': (UploadFile, 'Attach one operator-approved local file to a current file input. The path must be relative to BROWSER_UPLOAD_ROOT; arbitrary filesystem paths are forbidden. Uploads always require human confirmation.'),
    'select_option': (SelectOption, 'Select an option in a current <select> by its visible label or value. Use the options shown in the current observation.'),
    'press_key': (Press, 'Press a keyboard key.'),
    'scroll': (Scroll, 'Scroll viewport vertically.'),
    'go_back': (Args, 'Go back in browser history.'),
    'go_forward': (Args, 'Go forward in browser history.'),
    'read_page': (Read, 'Read a 12000-character chunk of page text; use offset for more.'),
    'wait': (Wait, 'Wait briefly for asynchronous UI changes.'),
    'ask_user': (Question, 'Ask for missing information; resume the same task.'),
    'finish': (Finish, 'Return an evidenced answer or explicitly report a blocker.'),
    'update_plan': (Plan, 'Create or revise a short high-level plan, never private reasoning.'),
    'remember': (Note, 'Retain an observed fact. The runtime attaches the current source URL and title.'),
}

def schemas():
    return [{'type': 'function', 'function': {'name': name, 'description': desc,
            'parameters': model.model_json_schema()}} for name, (model, desc) in REGISTRY.items()]

def parse_call(name: str, arguments: str):
    if name not in REGISTRY:
        raise ValueError('Unknown tool')
    return REGISTRY[name][0].model_validate(json.loads(arguments))

"""Opt-in read-only contract check against an existing sibling checkout; no service calls."""
import json
import os
import subprocess
import sys
import pytest
from evorove_lead.candidate import Candidate
from evorove_lead.handoff import Cycle1Handoff
from evorove_lead.crm_touch import assembled_touch, stable_person_id


@pytest.mark.skipif(not os.getenv("EVOROVE_CRM_REPO"), reason="sibling CRM checkout not explicitly selected")
def test_current_crm_accepts_actual_cycle_one_payload():
    candidate = Candidate(identity="test.person@example.com", reason="Published request for service scheduling support.", reason_source="https://example.com/need")
    person = stable_person_id("synthetic-tenant", email=candidate.identity)
    handoff = Cycle1Handoff.from_candidate(candidate, "email", person_id=person, hypothesis_id="test-hypothesis")
    payload = assembled_touch("synthetic-tenant", candidate, handoff, segment="Synthetic SMB")
    script = '''
import ast,json,sys
from pathlib import Path
from typing import Annotated,Any,Literal
from pydantic import BaseModel,ConfigDict,Field,AwareDatetime
# Load the actual request classes without importing src.api.__init__, which
# starts the application and may read deployment configuration.
tree=ast.parse(Path("src/api/schemas.py").read_text())
classes=[n for n in tree.body if isinstance(n,ast.ClassDef) and n.name in
         {"ApiModel","LeadTouchIdentityRequest","LeadTouchRequest"}]
assert len(classes)==3
exec(compile(ast.Module(body=classes,type_ignores=[]),"crm-request-contract","exec"))
from src.domain.lead_touch import LeadTouch, BoardTab, next_board_tab, stable_person_id
p=LeadTouchRequest.model_validate(json.load(sys.stdin))
t=LeadTouch.from_mapping("synthetic-tenant",p.model_dump())
assert next_board_tab(BoardTab.COLD,t.kind)==BoardTab.COLD
assert t.person_id==stable_person_id("synthetic-tenant",email="test.person@example.com")
assert t.payload["hypothesis_id"]=="test-hypothesis"
assert t.touch_id=="cycle1:assembled:"+t.person_id
print("crm_contract_valid")
'''
    result = subprocess.run([sys.executable, "-B", "-c", script], input=json.dumps(payload),
                            cwd=os.environ["EVOROVE_CRM_REPO"], capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "crm_contract_valid"

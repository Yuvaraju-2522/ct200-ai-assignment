from pydantic import BaseModel, Field
from typing import Literal, Any

class IngestResponse(BaseModel):
    version: int
    filename: str
    node_count: int
    changed_nodes: int
    new_nodes: int
    unchanged_nodes: int

class SelectionCreate(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    version: int
    node_ids: list[str] = Field(min_length=1)

class GenerateRequest(BaseModel):
    selection_id: str
    prompt: str = "Generate safety-focused, concrete QA test cases from the selected CT-200 manual content."
    count: int = Field(default=4, ge=3, le=5)

class TestCase(BaseModel):
    title: str
    preconditions: list[str]
    steps: list[str]
    expected_result: str
    source_node_ids: list[str]

class GenerationResponse(BaseModel):
    generation_id: str
    selection_id: str
    version: int
    cases: list[TestCase]
    status: Literal["valid", "invalid"]
    validation_errors: list[str]
    staleness: dict[str, Any]

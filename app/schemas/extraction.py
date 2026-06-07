from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field, StrictStr


class ConceptNode(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    id: Annotated[StrictStr, Field(min_length=1)]
    name: Annotated[StrictStr, Field(min_length=1)]
    type: Annotated[StrictStr, Field(min_length=1)]
    description: StrictStr = ""


class ConceptRelationship(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    source_node_id: Annotated[StrictStr, Field(min_length=1)]
    target_node_id: Annotated[StrictStr, Field(min_length=1)]
    relation_type: Annotated[StrictStr, Field(min_length=1)]


class GraphExtractionResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    nodes: list[ConceptNode] = Field(default_factory=list)
    relationships: list[ConceptRelationship] = Field(default_factory=list)

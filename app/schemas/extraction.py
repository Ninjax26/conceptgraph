from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field, StrictStr, model_validator


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

    @model_validator(mode="after")
    def validate_graph_integrity(self) -> "GraphExtractionResponse":
        node_ids = [node.id for node in self.nodes]
        if len(node_ids) != len(set(node_ids)):
            raise ValueError("Graph extraction contains duplicate concept IDs.")

        known_ids = set(node_ids)
        missing_endpoints = {
            endpoint
            for relationship in self.relationships
            for endpoint in (relationship.source_node_id, relationship.target_node_id)
            if endpoint not in known_ids
        }
        if missing_endpoints:
            raise ValueError("Graph relationships reference concepts that were not extracted.")

        unique_relationships: list[ConceptRelationship] = []
        seen: set[tuple[str, str, str]] = set()
        for relationship in self.relationships:
            key = (
                relationship.source_node_id,
                relationship.target_node_id,
                relationship.relation_type.strip().upper(),
            )
            if key in seen:
                continue
            seen.add(key)
            unique_relationships.append(relationship)
        self.relationships = unique_relationships
        return self

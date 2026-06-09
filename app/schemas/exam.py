"""Pydantic schemas for the Syllabus-Bounded Exam Generator.

These models enforce strict structure on both the LLM output and the API
response so that generated exams are always valid, well-typed JSON.
"""

from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field, StrictInt, StrictStr, model_validator


class MockQuestion(BaseModel):
    """A single multiple-choice question produced by the exam generator."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    question_text: Annotated[StrictStr, Field(min_length=1, description="The question prompt.")]
    options: Annotated[
        list[StrictStr],
        Field(min_length=4, max_length=4, description="Exactly four answer choices."),
    ]
    correct_answer: Annotated[
        StrictStr,
        Field(min_length=1, description="The correct option – must match one of the four options."),
    ]
    explanation: Annotated[
        StrictStr,
        Field(
            min_length=1,
            description="Explanation citing why the answer is correct based on the syllabus content.",
        ),
    ]

    @model_validator(mode="after")
    def correct_answer_must_match_an_option(self) -> "MockQuestion":
        if self.correct_answer not in self.options:
            raise ValueError(
                f"correct_answer '{self.correct_answer}' does not match any of the "
                f"provided options: {self.options}"
            )
        return self


class ExamResponse(BaseModel):
    """Structured response for a generated exam."""

    model_config = ConfigDict(extra="forbid")

    course_id: Annotated[StrictStr, Field(min_length=1)]
    week_number: Annotated[StrictInt, Field(ge=1)]
    questions: list[MockQuestion] = Field(default_factory=list)

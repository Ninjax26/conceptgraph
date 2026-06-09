"""API endpoint for the Syllabus-Bounded Exam Generator."""

from functools import lru_cache

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict, Field

from app.schemas.exam import ExamResponse
from app.services.exam_service import ExamService

router = APIRouter(prefix="/api/v1/exam", tags=["exam"])


class ExamGenerateRequest(BaseModel):
    """Request body for the exam generation endpoint."""

    model_config = ConfigDict(str_strip_whitespace=True)

    course_id: str = Field(..., min_length=1, description="Identifier for the course/document.")
    week_number: int = Field(..., ge=1, description="Week number to scope the exam to.")
    num_questions: int = Field(
        default=5,
        ge=1,
        le=20,
        description="Number of MCQs to generate (1-20).",
    )


@lru_cache
def get_exam_service() -> ExamService:
    return ExamService()


@router.post(
    "/generate",
    response_model=ExamResponse,
    summary="Generate a syllabus-bounded mock exam",
    description=(
        "Retrieves text chunks matching the given course_id and week_number "
        "from the Qdrant vector store via metadata filtering, then instructs "
        "the LLM to produce a structured multiple-choice exam strictly from "
        "that content."
    ),
)
async def generate_exam(
    request: ExamGenerateRequest,
    exam_service: ExamService = Depends(get_exam_service),
) -> ExamResponse:
    try:
        result = await exam_service.generate_exam(
            course_id=request.course_id,
            week_number=request.week_number,
            num_questions=request.num_questions,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(
            status_code=500,
            detail=f"Exam generation failed: {exc}",
        ) from exc

    return result

from dataclasses import dataclass
from uuid import NAMESPACE_URL, uuid5

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.processing import ProcessingStage, normalize_course_name
from app.models.document_upload import Course, DocumentUpload


class CourseNotFoundError(LookupError):
    pass


class CourseNotReadyError(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class ReadyCourseContext:
    course: Course
    documents: tuple[DocumentUpload, ...]

    @property
    def document_ids(self) -> list[str]:
        return [document.upload_id for document in self.documents]

    @property
    def graph_course_ids(self) -> list[str]:
        aliases = {self.course.id, self.course.display_name}
        aliases.update(document.course_id for document in self.documents)
        return sorted(aliases)


class CourseService:
    async def get_or_create(self, session: AsyncSession, name: str) -> Course:
        normalized = normalize_course_name(name)
        if not normalized:
            raise ValueError("Course name cannot be empty.")
        existing = await self.resolve(session, name, required=False)
        if existing is not None:
            return existing
        course = Course(
            id=str(uuid5(NAMESPACE_URL, f"conceptgraph:course:{normalized}")),
            normalized_name=normalized,
            display_name=name.strip().upper(),
        )
        session.add(course)
        await session.flush()
        return course

    async def resolve(
        self,
        session: AsyncSession,
        name_or_id: str,
        *,
        required: bool = True,
    ) -> Course | None:
        normalized = normalize_course_name(name_or_id)
        result = await session.execute(
            select(Course).where(
                (Course.id == name_or_id.strip()) | (Course.normalized_name == normalized)
            )
        )
        course = result.scalar_one_or_none()
        if course is None and required:
            raise CourseNotFoundError("Course not found.")
        return course

    async def get_ready_context(
        self,
        session: AsyncSession,
        name_or_id: str,
    ) -> ReadyCourseContext:
        course = await self.resolve(session, name_or_id)
        assert course is not None
        result = await session.execute(
            select(DocumentUpload)
            .where(
                DocumentUpload.course_uuid == course.id,
                DocumentUpload.stage == ProcessingStage.READY.value,
                DocumentUpload.processed_chunk_count > 0,
            )
            .order_by(DocumentUpload.created_at.desc())
        )
        documents = tuple(result.scalars().all())
        if not documents:
            raise CourseNotReadyError(
                "This course has no ready documents. Finish processing a PDF before continuing."
            )
        return ReadyCourseContext(course=course, documents=documents)

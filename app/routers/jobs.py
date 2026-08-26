from fastapi import APIRouter, BackgroundTasks, HTTPException, UploadFile
from fastapi.responses import Response

from app.job_store import job_store
from app.jobs_service import process_job

router = APIRouter(prefix="/api/jobs", tags=["jobs"])

DOCX_MEDIA_TYPE = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"


@router.post("", status_code=202)
async def create_job(background_tasks: BackgroundTasks, file: UploadFile):
    filename = file.filename or ""
    if not filename.lower().endswith(".docx"):
        raise HTTPException(status_code=400, detail="Only .docx files are supported.")

    data = await file.read()
    job = job_store.create(filename=filename)
    background_tasks.add_task(process_job, job, data)
    return {"job_id": job.id}


@router.get("/{job_id}")
def get_job(job_id: str):
    job = job_store.get(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="Job not found.")
    return job.to_dict()


@router.get("/{job_id}/download")
def download_job(job_id: str):
    job = job_store.get(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="Job not found.")
    if job.output_bytes is None:
        raise HTTPException(status_code=409, detail="Result not ready yet.")

    safe_name = (job.filename or "document.docx").rsplit("\\", 1)[-1].rsplit("/", 1)[-1]
    disposition_name = f"humanized_{safe_name}"
    quoted = disposition_name.replace('"', "'")
    return Response(
        content=job.output_bytes,
        media_type=DOCX_MEDIA_TYPE,
        headers={
            "Content-Disposition": f'attachment; filename="{quoted}"'
        },
    )

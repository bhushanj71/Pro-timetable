"""Files attached to a task: what may be uploaded, by whom, and how it comes back.

Two rules decide everything here.

Who may attach: whoever is doing the work, and whoever is managing it. That
means the people assigned to the task, its creator, and the community's
owner and admins -- nobody else, not even another member of the same
community, because a task is not a noticeboard.

What may be attached: an allow-list of extensions, checked against a matching
content type, and a size cap. A deny-list is the wrong shape for this problem
-- it is a list of the attacks somebody thought of.
"""
from __future__ import annotations

from fastapi import HTTPException, status

from app.models import (
    AssignmentStatus,
    CommunityRole,
    TaskAttachment,
    User,
    WorkTask,
)

# 10 MB. The bytes go in the database, so this is the number that keeps a
# report reasonable and a table from becoming a filesystem.
MAX_BYTES = 10 * 1024 * 1024

# Extension -> the content types that are allowed to carry it. The browser's
# reported type is a hint, not evidence, so both have to agree; a .pdf
# arriving as an executable content type is refused rather than guessed at.
ALLOWED = {
    "pdf": {"application/pdf"},
    "doc": {"application/msword"},
    "docx": {"application/vnd.openxmlformats-officedocument.wordprocessingml.document"},
    "xls": {"application/vnd.ms-excel"},
    "xlsx": {"application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"},
    "ppt": {"application/vnd.ms-powerpoint"},
    "pptx": {"application/vnd.openxmlformats-officedocument.presentationml.presentation"},
    "jpg": {"image/jpeg"},
    "jpeg": {"image/jpeg"},
    "png": {"image/png"},
    "zip": {"application/zip", "application/x-zip-compressed"},
    "txt": {"text/plain"},
}

# What a browser may be trusted to render in place rather than download. An
# open-in-tab for anything else is a way to get script running on this origin.
INLINE_SAFE = {"application/pdf", "image/jpeg", "image/png", "text/plain"}

ICONS = {
    "pdf": "📄", "doc": "📝", "docx": "📝", "xls": "📊", "xlsx": "📊",
    "ppt": "📽", "pptx": "📽", "jpg": "🖼", "jpeg": "🖼", "png": "🖼",
    "zip": "🗜", "txt": "📃",
}


def extension_of(file_name: str) -> str:
    return file_name.rsplit(".", 1)[-1].lower() if "." in file_name else ""


def safe_name(file_name: str) -> str:
    """The basename, with path separators removed.

    An uploaded name is attacker-controlled text. It is only ever shown and
    sent in a header here, never used to open anything, but stripping the
    path keeps "../../etc/passwd" from being displayed as though it were a
    real filename and from reaching a Content-Disposition intact.
    """
    name = (file_name or "").replace("\\", "/").rsplit("/", 1)[-1].strip()
    name = "".join(c for c in name if c.isprintable() and c not in '"\r\n')
    return name[:255] or "attachment"


def validate(file_name: str, content_type: str, size: int) -> tuple[str, str]:
    """Returns the cleaned (name, content_type), or raises with a plain reason."""
    name = safe_name(file_name)
    ext = extension_of(name)

    if ext not in ALLOWED:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            f"“.{ext or '?'}” files are not accepted. Attach a PDF, Word, Excel, "
            "PowerPoint, image, ZIP or text file.",
        )
    if size <= 0:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "That file is empty.")
    if size > MAX_BYTES:
        raise HTTPException(
            status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            f"“{name}” is {size / 1024 / 1024:.1f} MB. The limit is "
            f"{MAX_BYTES // 1024 // 1024} MB.",
        )

    declared = (content_type or "").split(";")[0].strip().lower()
    allowed = ALLOWED[ext]
    if declared and declared not in allowed:
        # Some browsers send nothing at all for less common types, which is
        # why an empty type falls through to the extension's own. A wrong one
        # is a different matter and is refused.
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            f"“{name}” says it is {declared}, which does not match a .{ext} file.",
        )
    return name, declared or sorted(allowed)[0]


# --------------------------------------------------------------------------
# Permissions
# --------------------------------------------------------------------------
def may_attach(task: WorkTask, user: User, member) -> bool:
    """Assignees do the work; the creator and community admins manage it.

    Declining is excluded deliberately. A declined assignment is still a row
    on the task, so a plain "are they assigned" check let somebody who had
    turned the work down keep attaching files to it -- which is neither doing
    the work nor managing it.
    """
    if member is None:
        return False
    if task.created_by == user.id:
        return True
    if member.role != CommunityRole.MEMBER.value:
        return True
    return any(
        a.user_id == user.id and a.status != AssignmentStatus.DECLINED.value
        for a in task.assignments
    )


def may_delete(attachment: TaskAttachment, task: WorkTask, user: User, member) -> bool:
    """Your own file, or anything if you manage the task.

    A member cannot remove the owner's brief, and cannot remove another
    member's evidence -- only their own.
    """
    if member is None:
        return False
    if attachment.uploaded_by == user.id:
        return True
    if task.created_by == user.id:
        return True
    return member.role != CommunityRole.MEMBER.value


def human_size(n: int) -> str:
    if n < 1024:
        return f"{n} B"
    if n < 1024 * 1024:
        return f"{n / 1024:.0f} KB"
    return f"{n / 1024 / 1024:.1f} MB"


def attachment_dict(a: TaskAttachment, *, person) -> dict:
    ext = extension_of(a.file_name)
    return {
        "id": a.id,
        "task_id": a.task_id,
        "file_name": a.file_name,
        "content_type": a.content_type,
        "size_bytes": a.size_bytes,
        "size": human_size(a.size_bytes),
        "icon": ICONS.get(ext, "📎"),
        "extension": ext,
        "can_view_inline": a.content_type in INLINE_SAFE,
        "uploaded_by": person(a.uploader),
        "uploaded_at": a.uploaded_at.isoformat(),
    }

"""`SqlBackend` : fichiers servis à l'agent depuis des tables SQL, sous Row-Level Security.

Deep Agents expose à l'agent un système de fichiers ; `CompositeBackend` route des préfixes vers des
backends. Ici, `/skills/` (et plus tard `/memories/` et `/group-memory/`) pointent vers un `SqlBackend`
adossé à un `FileStore`. La lecture seule est imposée par le backend lui-même : l'agent ne peut jamais
écrire dans les skills ni dans la mémoire de groupe (FR-006a, FR-017a)."""

from __future__ import annotations

import base64
import uuid
from typing import Any, Protocol

from deepagents.backends import CompositeBackend, StateBackend
from deepagents.backends.protocol import (
    BackendProtocol,
    DeleteResult,
    EditResult,
    FileData,
    FileDownloadResponse,
    FileInfo,
    FileUploadResponse,
    GlobResult,
    GrepResult,
    LsResult,
    ReadResult,
    WriteResult,
)
from deepagents.backends.utils import (
    InvalidGlobPatternError,
    _glob_search_files,
    create_file_data,
    file_data_to_string,
    grep_matches_from_files,
    perform_string_replacement,
    slice_read_response,
)
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from harness.db.models.catalog import Tool, ToolKind, ToolStatus
from harness.db.session import group_context

READ_ONLY_ERROR = "Erreur : ce dossier est en lecture seule, aucune modification n'est possible."


class FileStore(Protocol):
    """Source de vérité des fichiers d'une route : chemins absolus relatifs à la route."""

    read_only: bool

    async def load(self) -> dict[str, FileData]: ...

    async def save(self, path: str, content: str) -> str | None:
        """Crée ou remplace un fichier ; renvoie un message d'erreur ou `None`."""
        ...

    async def remove(self, path: str) -> str | None: ...


class SqlBackend(BackendProtocol):
    def __init__(self, store: FileStore) -> None:
        self._store = store

    @property
    def read_only(self) -> bool:
        return self._store.read_only

    # Les exécutions sont asynchrones : les variantes synchrones n'ont pas d'usage.
    def _sync(self) -> Any:
        raise NotImplementedError("SqlBackend est asynchrone (utiliser les méthodes a*)")

    def ls(self, path: str) -> LsResult:
        return self._sync()  # type: ignore[no-any-return]

    def read(self, file_path: str, offset: int = 0, limit: int = 2000) -> ReadResult:
        return self._sync()  # type: ignore[no-any-return]

    def write(self, file_path: str, content: str) -> WriteResult:
        return self._sync()  # type: ignore[no-any-return]

    def edit(self, file_path: str, old_string: str, new_string: str, replace_all: bool = False) -> EditResult:  # noqa: FBT001, FBT002
        return self._sync()  # type: ignore[no-any-return]

    def delete(self, file_path: str) -> DeleteResult:
        return self._sync()  # type: ignore[no-any-return]

    def grep(self, pattern: str, path: str | None = None, glob: str | None = None, *, max_count: int | None = None) -> GrepResult:
        return self._sync()  # type: ignore[no-any-return]

    def glob(self, pattern: str, path: str | None = None) -> GlobResult:
        return self._sync()  # type: ignore[no-any-return]

    async def als(self, path: str) -> LsResult:
        files = await self._store.load()
        base = path if path.endswith("/") else path + "/"
        infos: list[FileInfo] = []
        subdirs: set[str] = set()
        for key, data in files.items():
            if not key.startswith(base):
                continue
            rest = key[len(base) :]
            if "/" in rest:
                subdirs.add(base + rest.split("/")[0] + "/")
                continue
            infos.append(
                {
                    "path": key,
                    "is_dir": False,
                    "size": len(file_data_to_string(data).encode()),
                    "modified_at": data.get("modified_at", ""),
                }
            )
        infos.extend(FileInfo(path=d, is_dir=True, size=0, modified_at="") for d in sorted(subdirs))
        infos.sort(key=lambda i: i["path"])
        return LsResult(entries=infos)

    async def aread(self, file_path: str, offset: int = 0, limit: int = 2000) -> ReadResult:
        data = (await self._store.load()).get(file_path)
        if data is None:
            return ReadResult(error=f"Fichier introuvable : « {file_path} »")
        return slice_read_response(data, offset, limit)

    async def awrite(self, file_path: str, content: str) -> WriteResult:
        if self.read_only:
            return WriteResult(error=READ_ONLY_ERROR)
        error = await self._store.save(file_path, content)
        return WriteResult(error=error) if error else WriteResult(path=file_path)

    async def aedit(self, file_path: str, old_string: str, new_string: str, replace_all: bool = False) -> EditResult:  # noqa: FBT001, FBT002
        if self.read_only:
            return EditResult(error=READ_ONLY_ERROR)
        data = (await self._store.load()).get(file_path)
        if data is None:
            return EditResult(error=f"Fichier introuvable : « {file_path} »")
        result = perform_string_replacement(file_data_to_string(data), old_string, new_string, replace_all)
        if isinstance(result, str):
            return EditResult(error=result)
        content, occurrences = result
        error = await self._store.save(file_path, content)
        return EditResult(error=error) if error else EditResult(path=file_path, occurrences=int(occurrences))

    async def adelete(self, file_path: str) -> DeleteResult:
        if self.read_only:
            return DeleteResult(error=READ_ONLY_ERROR)
        error = await self._store.remove(file_path)
        return DeleteResult(error=error) if error else DeleteResult(path=file_path)

    async def agrep(
        self, pattern: str, path: str | None = None, glob: str | None = None, *, max_count: int | None = None
    ) -> GrepResult:
        files = await self._store.load()
        return grep_matches_from_files(files, pattern, path if path is not None else "/", glob, max_count=max_count)

    async def aglob(self, pattern: str, path: str | None = None) -> GlobResult:
        files = await self._store.load()
        try:
            found = _glob_search_files(files, pattern, path)
        except InvalidGlobPatternError as exc:
            return GlobResult(error=str(exc))
        if found == "No files found":
            return GlobResult(matches=[])
        infos: list[FileInfo] = []
        for p in found.split("\n"):
            data = files.get(p)
            infos.append(
                {
                    "path": p,
                    "is_dir": False,
                    "size": len(file_data_to_string(data).encode()) if data else 0,
                    "modified_at": data.get("modified_at", "") if data else "",
                }
            )
        return GlobResult(matches=infos)

    async def aupload_files(self, files: list[tuple[str, bytes]]) -> list[FileUploadResponse]:
        if self.read_only:
            return [FileUploadResponse(path=p, error="permission_denied") for p, _ in files]
        out = []
        for path, content in files:
            error = await self._store.save(path, content.decode("utf-8", errors="replace"))
            out.append(FileUploadResponse(path=path, error=error))
        return out

    async def adownload_files(self, paths: list[str]) -> list[FileDownloadResponse]:
        files = await self._store.load()
        out = []
        for path in paths:
            data = files.get(path)
            if data is None:
                out.append(FileDownloadResponse(path=path, content=None, error="file_not_found"))
                continue
            text = file_data_to_string(data)
            raw = text.encode() if data.get("encoding", "utf-8") == "utf-8" else base64.b64decode(text)
            out.append(FileDownloadResponse(path=path, content=raw, error=None))
        return out


class SkillFileStore:
    """Fichiers des skills autorisées (`Tool.files`), en lecture seule : `/<skill>/<fichier>`."""

    read_only = True

    def __init__(self, sessionmaker: async_sessionmaker[AsyncSession], group_id: uuid.UUID, tool_ids: list[uuid.UUID]) -> None:
        self._sessionmaker = sessionmaker
        self._group_id = group_id
        self._tool_ids = tool_ids

    async def load(self) -> dict[str, FileData]:
        async with self._sessionmaker() as session, session.begin():
            await group_context(session, self._group_id)
            rows = (
                await session.execute(
                    select(Tool).where(
                        Tool.id.in_(self._tool_ids), Tool.kind == ToolKind.SKILL, Tool.status == ToolStatus.ACTIVE
                    )
                )
            ).scalars()
            return {
                f"/{tool.skill_name}/{path}": create_file_data(content)
                for tool in rows
                for path, content in (tool.files or {}).items()
            }

    async def save(self, path: str, content: str) -> str | None:
        return READ_ONLY_ERROR

    async def remove(self, path: str) -> str | None:
        return READ_ONLY_ERROR


def build_backend(routes: dict[str, FileStore]) -> BackendProtocol | None:
    """`CompositeBackend` : fichiers de travail éphémères par défaut, et une route par `FileStore`."""
    if not routes:
        return None
    return CompositeBackend(
        default=StateBackend(), routes={prefix: SqlBackend(store) for prefix, store in routes.items()}
    )

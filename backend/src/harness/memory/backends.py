"""`SqlBackend` : fichiers servis à l'agent depuis des tables SQL, sous Row-Level Security.

Deep Agents expose à l'agent un système de fichiers ; `CompositeBackend` route des préfixes vers des
backends. Ici, `/skills/` (et plus tard `/memories/` et `/group-memory/`) pointent vers un `SqlBackend`
adossé à un `FileStore`. La lecture seule est imposée par le backend lui-même : l'agent ne peut jamais
écrire dans les skills ni dans la mémoire de groupe (FR-006a, FR-017a)."""

from __future__ import annotations

import base64
import contextlib
import uuid
from collections.abc import AsyncIterator
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
from harness.db.models.memory import MemoryScope
from harness.db.session import group_context
from harness.memory import service as memory
from harness.memory.service import Actor
from harness.runtime.context import EventSink

READ_ONLY_ERROR = "Erreur : ce dossier est en lecture seule, aucune modification n'est possible."


class FileStore(Protocol):
    """Source de vérité des fichiers d'une route : chemins absolus relatifs à la route."""

    read_only: bool

    async def load(self) -> dict[str, FileData]: ...

    async def save(self, path: str, content: str) -> str | None:
        """Crée ou remplace un fichier ; renvoie un message d'erreur ou `None`."""
        ...

    async def remove(self, path: str) -> str | None: ...


class _Observed(Protocol):
    """Capacités facultatives d'un magasin qui suit les lectures de l'agent (mémoire)."""

    async def note_read(self, path: str) -> None: ...

    async def precheck_edit(self, path: str, old: str, new: str) -> str | None: ...


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
        if (note := getattr(self._store, "note_read", None)) is not None:
            await note(file_path)
        return slice_read_response(data, offset, limit)

    async def awrite(self, file_path: str, content: str) -> WriteResult:
        if self.read_only:
            return WriteResult(error=READ_ONLY_ERROR)
        error = await self._store.save(file_path, content)
        return WriteResult(error=error) if error else WriteResult(path=file_path)

    async def aedit(self, file_path: str, old_string: str, new_string: str, replace_all: bool = False) -> EditResult:  # noqa: FBT001, FBT002
        if self.read_only:
            return EditResult(error=READ_ONLY_ERROR)
        if (precheck := getattr(self._store, "precheck_edit", None)) is not None:
            if (conflict := await precheck(file_path, old_string, new_string)) is not None:
                return EditResult(error=conflict)
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


class MemoryFileStore:
    """Entrées de mémoire d'une portée, vues comme des fichiers `/<chemin>`.

    - `agent` : mémoire propre à l'agent courant, en lecture et écriture ; l'agent ne voit jamais celle
      d'un autre agent (filtre par `agent_id`, puis RLS par groupe) ;
    - `group` : mémoire de groupe, en lecture seule (FR-017a : seules les propositions validées l'alimentent).

    Chaque lecture de l'agent est mémorisée (`note_read`) : l'écriture qui suit est conditionnée à la
    version lue, de sorte qu'une modification faite entre-temps par une autre exécution produise un
    conflit visible plutôt qu'un écrasement silencieux."""

    def __init__(
        self,
        sessionmaker: async_sessionmaker[AsyncSession],
        *,
        group_id: uuid.UUID,
        scope: str,
        agent_id: uuid.UUID | None,
        run_id: uuid.UUID | None,
        sink: EventSink | None,
        read_only: bool,
    ) -> None:
        self._sessionmaker = sessionmaker
        self._group_id, self._scope, self._agent_id = group_id, scope, agent_id
        self._run_id, self._sink = run_id, sink
        self.read_only = read_only
        self._versions: dict[str, int] = {}
        self._seen: dict[str, int] = {}

    @contextlib.asynccontextmanager
    async def _txn(self) -> AsyncIterator[AsyncSession]:
        async with self._sessionmaker() as session, session.begin():
            await group_context(session, self._group_id)
            yield session

    async def _emit(self, type_: str, payload: dict[str, Any]) -> None:
        if self._sink is not None:
            await self._sink.emit(type_, payload)

    @property
    def _actor(self) -> Actor:
        return Actor("agent", self._agent_id)

    async def load(self) -> dict[str, FileData]:
        async with self._txn() as session:
            entries = await memory.list_entries(session, scope=self._scope, agent_id=self._agent_id)
        self._versions = {e.path: e.version for e in entries}
        return {
            f"/{e.path}": {
                "content": e.content,
                "encoding": "utf-8",
                "created_at": e.created_at.isoformat(),
                "modified_at": e.updated_at.isoformat(),
            }
            for e in entries
        }

    @staticmethod
    def _key(path: str) -> str:
        return memory.normalize_path(path)

    async def note_read(self, path: str) -> None:
        key = self._key(path)
        version = self._versions.get(key)
        if version is None:
            return
        self._seen[key] = version
        await self._emit("memory_read", {"scope": self._scope, "path": key, "version": version})

    async def _conflict_message(self, key: str, expected: int, actual: int) -> str:
        await self._emit("memory_conflict", {"path": key, "expected_version": expected, "actual_version": actual})
        return (
            f"Conflit : « {key} » a été modifiée par une autre exécution (version lue {expected}, version "
            f"actuelle {actual}). Relis le fichier puis réessaie ; ta tentative est conservée dans l'historique."
        )

    async def precheck_edit(self, path: str, old: str, new: str) -> str | None:
        try:
            key = self._key(path)
        except memory.MemoryPathInvalid as exc:
            return str(exc)
        expected = self._seen.get(key)
        if expected is None:
            return None
        async with self._txn() as session:
            entry = await memory.find_entry(session, scope=self._scope, agent_id=self._agent_id, path=key, lock=True)
            if entry is None or entry.deleted_at is not None or entry.version == expected:
                return None
            await memory.record_conflict(session, entry, self._actor, f"edit_file : « {old} » → « {new} »", expected)
            actual = entry.version
        return await self._conflict_message(key, expected, actual)

    async def save(self, path: str, content: str) -> str | None:
        if self.read_only:
            return READ_ONLY_ERROR
        try:
            key = self._key(path)
        except memory.MemoryPathInvalid as exc:
            return str(exc)
        expected = self._seen.get(key)
        try:
            async with self._txn() as session:
                outcome = await memory.write_entry(
                    session, group_id=self._group_id, scope=self._scope, agent_id=self._agent_id, path=key,
                    content=content, expected_version=expected, actor=self._actor, source_run_id=self._run_id,
                )
        except memory.MemoryTooLarge as exc:
            return f"Erreur : {exc}"
        if outcome.conflict:
            return await self._conflict_message(key, outcome.expected_version or 0, outcome.actual_version or 0)
        self._seen[key] = self._versions[key] = outcome.entry.version
        await self._emit(
            "memory_written",
            {"scope": self._scope, "path": key, "version": outcome.entry.version, "change_type": outcome.change_type},
        )
        return None

    async def remove(self, path: str) -> str | None:
        if self.read_only:
            return READ_ONLY_ERROR
        try:
            key = self._key(path.rstrip("/"))
        except memory.MemoryPathInvalid as exc:
            return str(exc)
        removed: list[Any] = []
        async with self._txn() as session:
            for entry in await memory.list_entries(session, scope=self._scope, agent_id=self._agent_id):
                if entry.path == key or entry.path.startswith(key + "/"):
                    removed.append(await memory.delete_entry(session, entry, self._actor))
        if not removed:
            return f"Fichier introuvable : « {path} »"
        for entry in removed:
            self._seen.pop(entry.path, None)
            await self._emit(
                "memory_written",
                {"scope": self._scope, "path": entry.path, "version": entry.version, "change_type": "delete"},
            )
        return None


def build_run_backend(
    sessionmaker: async_sessionmaker[AsyncSession],
    *,
    group_id: uuid.UUID,
    agent_id: uuid.UUID,
    run_id: uuid.UUID | None,
    sink: EventSink | None,
    agent_memory: bool,
    group_memory: bool,
    skill_tool_ids: list[uuid.UUID],
) -> BackendProtocol | None:
    """Fichiers vus par l'agent : espace de travail éphémère par défaut, et une route par source —
    `/memories/` (lecture et écriture), `/group-memory/` et `/skills/` (lecture seule)."""
    routes: dict[str, FileStore] = {}
    if agent_memory:
        routes["/memories/"] = MemoryFileStore(
            sessionmaker, group_id=group_id, scope=MemoryScope.AGENT, agent_id=agent_id, run_id=run_id, sink=sink,
            read_only=False,
        )
    if group_memory:
        routes["/group-memory/"] = MemoryFileStore(
            sessionmaker, group_id=group_id, scope=MemoryScope.GROUP, agent_id=None, run_id=run_id, sink=sink,
            read_only=True,
        )
    if skill_tool_ids:
        routes["/skills/"] = SkillFileStore(sessionmaker, group_id, skill_tool_ids)
    return build_backend(routes)


def build_backend(routes: dict[str, FileStore]) -> BackendProtocol | None:
    """`CompositeBackend` : fichiers de travail éphémères par défaut, et une route par `FileStore`."""
    if not routes:
        return None
    return CompositeBackend(
        default=StateBackend(), routes={prefix: SqlBackend(store) for prefix, store in routes.items()}
    )

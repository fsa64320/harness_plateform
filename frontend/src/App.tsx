import { useQuery } from "@tanstack/react-query";
import { useEffect } from "react";
import { Link, Navigate, Route, Routes, useMatch } from "react-router-dom";

import { api, ApiError, LOGIN_URL, type Schemas } from "./api/client";
import ApprovalBadge from "./components/ApprovalBadge";
import GroupSwitcher from "./components/GroupSwitcher";
import AgentsPage from "./pages/AgentsPage";
import ApprovalsPage from "./pages/ApprovalsPage";
import RunPage from "./pages/RunPage";
import RunsPage from "./pages/RunsPage";

export const useMe = () =>
  useQuery({
    queryKey: ["me"],
    queryFn: () => api<Schemas["Me"]>("/me"),
    retry: (count, error) => !(error instanceof ApiError && error.status === 401) && count < 2,
  });

export default function App() {
  const { data: me, error, isLoading } = useMe();
  const match = useMatch("/g/:groupId/*");
  const groupId = match?.params.groupId;

  useEffect(() => {
    if (error instanceof ApiError && error.status === 401) window.location.assign(LOGIN_URL);
  }, [error]);

  if (isLoading || error) return <p role="status">Connexion…</p>;
  if (!me) return null;

  const memberships = me.memberships ?? [];
  const first = memberships[0];

  return (
    <>
      <header>
        <strong>Plateforme de harnais d'agents</strong>{" "}
        <GroupSwitcher memberships={memberships} currentGroupId={groupId} /> <span>{me.email}</span>
        {groupId && (
          <nav>
            <Link to={`/g/${groupId}/agents`}>Agents</Link>{" "}
            <Link to={`/g/${groupId}/runs`}>Exécutions</Link> <ApprovalBadge groupId={groupId} />
          </nav>
        )}
      </header>
      <main>
        <Routes>
          <Route
            path="/"
            element={first ? <Navigate to={`/g/${first.group_id}/agents`} replace /> : null}
          />
          <Route path="/g/:groupId/agents" element={<AgentsPage />} />
          <Route path="/g/:groupId/runs" element={<RunsPage />} />
          <Route path="/g/:groupId/runs/:runId" element={<RunPage />} />
          <Route path="/g/:groupId/approvals" element={<ApprovalsPage />} />
        </Routes>
      </main>
    </>
  );
}

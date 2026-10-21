import { useQuery } from "@tanstack/react-query";
import { useEffect } from "react";
import { Navigate, Route, Routes, useMatch } from "react-router-dom";

import { api, ApiError, LOGIN_URL, type Schemas } from "./api/client";
import GroupSwitcher from "./components/GroupSwitcher";

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
      </header>
      <main>
        <Routes>
          <Route
            path="/"
            element={first ? <Navigate to={`/g/${first.group_id}/agents`} replace /> : null}
          />
        </Routes>
      </main>
    </>
  );
}

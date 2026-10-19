"use client";

import { useEffect, useState } from "react";

import { api } from "@/lib/api";

/** Fetch `path` now and every `ms`; refetch immediately whenever `version` changes. */
export function usePoll<T>(path: string, version: number, ms = 4000) {
  const [data, setData] = useState<T | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let alive = true;
    const load = () =>
      api<T>(path)
        .then((d) => {
          if (alive) {
            setData(d);
            setError(null);
          }
        })
        .catch((e: Error) => {
          if (alive) setError(e.message);
        });
    load();
    const id = setInterval(load, ms);
    return () => {
      alive = false;
      clearInterval(id);
    };
  }, [path, version, ms]);

  return { data, error };
}

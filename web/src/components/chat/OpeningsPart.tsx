/**
 * The first emails, before they go.
 *
 * The prop list is the gate on what gets bought. This is the gate on what gets
 * *said*, and until now there wasn't one: the tick researched an item, opened
 * a negotiation and mailed a stranger on the next pass — a message written by
 * a model, sent from the producer's own mailbox, over their name, and read by
 * them afterwards if at all.
 *
 * So they are shown, and they are editable. Editable is the part that matters:
 * a producer who can only approve or refuse will approve, because refusing
 * costs them the whole negotiation. Being able to fix one sentence is what
 * makes reading them worth doing.
 *
 * Approved as a batch, with one button, for the same reason the props are: a
 * column of separate Send buttons invites sending half of them and leaves the
 * rest in a state nobody is coming back to.
 */

import type { ToolCallMessagePartProps } from "@assistant-ui/react";
import { useState } from "react";

import { releaseOpenings, editOpening } from "@/chat/api";
import type { PendingOpening } from "@/chat/rows";
import { useProjectId } from "@/components/chat/context";
import { Button } from "@/components/ui/button";

export interface OpeningsArgs {
  openings?: PendingOpening[];
}

export function OpeningsPart({
  args,
}: ToolCallMessagePartProps<OpeningsArgs, unknown>) {
  const projectId = useProjectId();
  return <Openings projectId={projectId} pending={args.openings ?? []} />;
}

export function Openings({
  projectId,
  pending,
}: {
  projectId: string;
  pending: PendingOpening[];
}) {
  const [edits, setEdits] = useState<
    Record<string, { subject: string; body: string; to: string }>
  >({});
  // Included by default, like the prop list. The producer's job is to catch
  // the one they do not want, not to re-approve the ones they do.
  const [dropped, setDropped] = useState<Record<string, boolean>>({});
  const [busy, setBusy] = useState(false);
  const [done, setDone] = useState("");
  const [error, setError] = useState("");

  if (pending.length === 0) return null;

  const textOf = (opening: PendingOpening) =>
    edits[opening.negotiationId] ?? {
      subject: opening.subject,
      body: opening.body,
      to: opening.to,
    };

  const keeping = (opening: PendingOpening) => dropped[opening.negotiationId] !== true;
  const kept = pending.filter(keeping).length;

  const send = () => {
    setBusy(true);
    setError("");
    // Saved before approving, and awaited: approving makes the row due, and a
    // tick can pick it up within the minute. An edit still in flight at that
    // moment would be a rewrite the supplier never sees.
    void (async () => {
      try {
        for (const opening of pending) {
          const edited = edits[opening.negotiationId];
          // Only the dirtied ones, and never a dropped one — saving a draft
          // about to be cancelled is a write nobody will ever read.
          if (edited === undefined || !keeping(opening)) continue;
          const saved = await editOpening(
            projectId,
            opening.negotiationId,
            edited.subject,
            edited.body,
            edited.to === opening.to ? "" : edited.to,
          );
          if (saved.kind === "error") {
            setError(saved.detail);
            return;
          }
        }
        const released = await releaseOpenings(
          projectId,
          pending.map((o) => ({
            negotiation_id: o.negotiationId,
            include: keeping(o),
          })),
        );
        if (released.kind === "error") {
          setError(released.detail);
          return;
        }
        const gone = pending.length - kept;
        setDone(
          `${kept} email${kept === 1 ? "" : "s"} on the way` +
            (gone > 0 ? `, ${gone} dropped` : "") +
            ". The agent takes it from here and answers the replies itself.",
        );
      } finally {
        setBusy(false);
      }
    })();
  };

  return (
    <div className="my-2 rounded-lg border px-4 py-3 text-sm">
      <p className="text-xs font-medium tracking-wide text-muted-foreground uppercase">
        Waiting to be sent
      </p>
      <p className="mt-1 font-medium">
        {pending.length} opening email{pending.length === 1 ? "" : "s"}
      </p>
      <p className="mt-1 text-xs text-muted-foreground">
        Written by the agent, from your mailbox, over your name. Nothing has
        gone yet — read them, change anything you like, untick any you do not
        want, then send.
      </p>

      <div className="mt-3 space-y-3">
        {pending.map((opening) => {
          const text = textOf(opening);
          const keep = keeping(opening);
          const locked = busy || done !== "" || !keep;
          const change = (next: Partial<{ subject: string; body: string; to: string }>) =>
            setEdits((prior) => ({
              ...prior,
              [opening.negotiationId]: { ...text, ...next },
            }));
          return (
            <div
              key={opening.negotiationId}
              className={`rounded-md border px-3 py-2 ${keep ? "" : "opacity-50"}`}
            >
              <div className="flex flex-wrap items-center gap-2">
                {/* Untick to drop it. Same control and same meaning as the
                    prop list: the seller is not written to, and the record
                    says so rather than the draft sitting in the queue
                    forever. */}
                <input
                  type="checkbox"
                  checked={keep}
                  disabled={busy || done !== ""}
                  onChange={(e) =>
                    setDropped((prior) => ({
                      ...prior,
                      [opening.negotiationId]: !e.target.checked,
                    }))
                  }
                />
                <p className="text-xs text-muted-foreground">
                  <span className="font-medium">{opening.supplier}</span> about{" "}
                  <span className="font-medium capitalize">{opening.itemName}</span>
                </p>
              </div>
              {/* The address, as an ordinary editable field. Point it at an
                  inbox you own and you can answer as the seller, which turns
                  a five-day negotiation into a minute — and because it is
                  shown rather than hidden behind an edit affordance, a
                  redirect is something you can see before you send. */}
              <label className="mt-2 flex items-center gap-2 text-xs text-muted-foreground">
                To
                <input
                  value={text.to}
                  disabled={locked}
                  onChange={(e) => change({ to: e.target.value })}
                  className="flex-1 rounded border bg-background px-2 py-1 font-mono"
                />
              </label>
              <input
                value={text.subject}
                disabled={locked}
                onChange={(e) => change({ subject: e.target.value })}
                className="mt-2 w-full rounded border bg-background px-2 py-1 text-sm font-medium"
              />
              <textarea
                value={text.body}
                rows={7}
                disabled={locked}
                onChange={(e) => change({ body: e.target.value })}
                className="mt-2 w-full resize-y rounded border bg-background px-2 py-1 font-mono text-xs"
              />
            </div>
          );
        })}
      </div>

      {done === "" ? (
        <div className="mt-3 flex items-center gap-3">
          <Button size="sm" loading={busy} disabled={kept === 0} onClick={send}>
            {busy ? "Sending…" : `Send ${kept}`}
          </Button>
          <span className="text-xs text-muted-foreground">
            {kept === 0
              ? "Everything here is unticked — nothing would be sent."
              : "Nothing reaches a seller until you do. Unticked ones are dropped."}
          </span>
        </div>
      ) : (
        <p className="mt-3 font-medium">{done}</p>
      )}
      {error !== "" && <p className="mt-2 text-xs text-destructive">{error}</p>}
    </div>
  );
}

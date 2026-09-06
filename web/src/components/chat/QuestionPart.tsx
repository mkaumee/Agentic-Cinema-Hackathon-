/**
 * The other reason the agent stops.
 *
 * Two things park a negotiation at `READY_FOR_HUMAN`, and only one of them is
 * about money. A seller who writes back asking *which* mirror, or for a photo
 * of the room, has asked a fair question that no amount of further reasoning
 * answers — the fact is in the producer's head, or on their phone, and nowhere
 * the agent can reach.
 *
 * So this card is not a decision. There is no Approve and no Reject: refusing
 * a question is not a thing that means anything to the person waiting on it.
 * There is a box, a file picker and a Send, and afterwards the agent goes back
 * to negotiating unattended.
 *
 * The words are sent as typed. The producer answered a question that was put
 * to them, and handing it to the brain to rephrase is how a seller ends up
 * being told something nobody said.
 *
 * Nothing is emailed from this page. It posts to `cinema-api`, which stores
 * the file and marks the row due; the tick service — the one holding the
 * mailbox — sends it within the minute.
 */

import type { ToolCallMessagePartProps } from "@assistant-ui/react";
import { useRef, useState } from "react";

import { answerSupplier, toUpload, type Upload } from "@/chat/api";
import { useProjectId } from "@/components/chat/context";
import { Button } from "@/components/ui/button";

export interface QuestionArgs {
  negotiationId?: string;
  item?: string;
  supplier?: string;
  asked?: string;
}

/** Ten megabytes, the same limit the API enforces.
 *
 * Checked here as well so a producer who picks a video learns it now rather
 * than after uploading it — the server is still the one that decides. */
const MAX_BYTES = 10 * 1024 * 1024;

export function QuestionPart({ args }: ToolCallMessagePartProps<QuestionArgs, unknown>) {
  const projectId = useProjectId();
  return (
    <Question
      args={args}
      negotiationId={args.negotiationId ?? ""}
      projectId={projectId}
    />
  );
}

export function Question({
  args,
  negotiationId,
  projectId,
  compact = false,
}: {
  args: QuestionArgs;
  negotiationId: string;
  projectId: string;
  /** The rail's version: same question, less around it. */
  compact?: boolean;
}) {
  const [answer, setAnswer] = useState("");
  const [file, setFile] = useState<File | null>(null);
  const [busy, setBusy] = useState(false);
  const [done, setDone] = useState("");
  const [error, setError] = useState("");
  const picker = useRef<HTMLInputElement>(null);

  const send = () => {
    if (answer.trim() === "" && file === null) {
      setError("Type an answer, attach a file, or both.");
      return;
    }
    setBusy(true);
    setError("");
    void (async () => {
      try {
        let upload: Upload | undefined;
        if (file !== null) {
          if (file.size > MAX_BYTES) {
            setError(`${file.name} is larger than 10 MB — too big to email.`);
            return;
          }
          upload = await toUpload(file);
        }
        const result = await answerSupplier(
          projectId,
          negotiationId,
          answer.trim(),
          upload,
        );
        if (result.kind === "done") {
          setDone(
            file === null
              ? "Sent to the agent. It replies on the next tick."
              : `Sent, with ${file.name} attached. It replies on the next tick.`,
          );
        } else {
          setError(result.detail);
        }
      } finally {
        setBusy(false);
      }
    })();
  };

  return (
    <div className="my-2 rounded-lg border-2 border-foreground/40 px-4 py-3 text-sm">
      <p className="text-xs font-medium tracking-wide text-muted-foreground uppercase">
        Needs you
      </p>
      {/* `capitalize` on the item only. On the whole line it title-cases the
          sentence — "Ah Seng Rentals Asked About Mirror" — which reads like a
          headline rather than like the agent telling you something. */}
      <p className="mt-1 font-medium">
        {args.supplier ?? "a seller"} asked about{" "}
        <span className="capitalize">{args.item ?? "an item"}</span>
      </p>
      {args.asked !== undefined && args.asked !== "" && (
        // Their question, carried through from the reply the agent read. In
        // their words, because a summary of a question is a good way to answer
        // a different one.
        <p className="mt-2 border-l-2 pl-3 text-muted-foreground italic">
          {args.asked}
        </p>
      )}

      {done === "" ? (
        <>
          <textarea
            value={answer}
            rows={compact ? 2 : 3}
            disabled={busy}
            placeholder="Answer them here. This goes out as you wrote it."
            onChange={(e) => setAnswer(e.target.value)}
            className="mt-3 w-full resize-y rounded border bg-background px-2 py-1 text-sm"
          />
          <input
            ref={picker}
            type="file"
            className="hidden"
            onChange={(e) => setFile(e.target.files?.[0] ?? null)}
          />
          <div className="mt-2 flex flex-wrap items-center gap-3">
            <Button size="sm" loading={busy} onClick={send}>
              {busy ? "Sending…" : "Send to seller"}
            </Button>
            <Button
              size="sm"
              variant="outline"
              disabled={busy}
              onClick={() => picker.current?.click()}
            >
              {file === null ? "Attach a file" : "Change file"}
            </Button>
            {file !== null && (
              <span className="text-xs text-muted-foreground">
                {file.name}{" "}
                <button
                  type="button"
                  className="underline"
                  onClick={() => setFile(null)}
                >
                  remove
                </button>
              </span>
            )}
          </div>
          {!compact && (
            <p className="mt-2 text-xs text-muted-foreground">
              Only this reply needs you. The agent carries on from here on its
              own, and still stops before anything is bought.
            </p>
          )}
        </>
      ) : (
        <p className="mt-3 font-medium">{done}</p>
      )}
      {error !== "" && <p className="mt-2 text-xs text-destructive">{error}</p>}
    </div>
  );
}
